"""CLI: python -m bazaar_agent {run,top,item,alert}"""
import argparse
import logging
import sys
import time

from .agent import BazaarAgent
from .config import Config
from .market import BazaarError, fetch_quotes
from .model import train
from .notify import Discord
from .store import Store
from .strategy import margin_per_unit


def cmd_run(cfg, args):
    BazaarAgent(cfg).run(once=args.once)


def cmd_top(cfg, args):
    """Raw top margins right now (no history, no risk model)."""
    quotes = fetch_quotes(cfg.api_key)
    rows = []
    for q in quotes.values():
        ppu = margin_per_unit(q, cfg.tax)
        flow = min(q.buy_flow, q.sell_flow)
        if ppu > 0 and flow >= cfg.min_daily_volume and ppu / q.bid <= cfg.max_margin:
            rows.append((ppu * flow * cfg.capture, ppu, ppu / q.bid, flow, q.name))
    for pday, ppu, pct, flow, name in sorted(rows, reverse=True)[: args.n]:
        print(f"{name:30} ppu {ppu:>11,.1f} ({pct:6.1%})  flow/day {flow:>9,.0f}  profit/day {pday:>13,.0f}")


def cmd_item(cfg, args):
    name = args.name.strip().upper()
    q = fetch_quotes(cfg.api_key).get(name)
    if not q:
        sys.exit(f"{name} not found in bazaar")
    ppu = margin_per_unit(q, cfg.tax)
    print(f"{name}\n  buy order (bid): {q.bid:,.1f}\n  sell offer (ask): {q.ask:,.1f}\n"
          f"  net profit/unit: {ppu:,.1f} ({ppu / q.bid:.1%})\n"
          f"  flow/day: buy {q.buy_flow:,.0f} | sell {q.sell_flow:,.0f}")


def cmd_stats(cfg, args):
    """What the database holds and how past recommendations turned out."""
    db = Store(cfg.data_dir / "bazaar.db").db
    n_prices, first, last = db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM prices").fetchone()
    n_recs = db.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    print(f"price rows: {n_prices:,}  recommendations: {n_recs:,}")
    if n_prices:
        print(f"price data spans {(last - first) / 3600:.1f} h")
    t, w = db.execute("SELECT COALESCE(SUM(trades),0), COALESCE(SUM(wins),0) FROM learning").fetchone()
    print(f"judged recommendations: {t}" + (f"  win rate {w / t:.0%}" if t else ""))
    rows = db.execute("SELECT item, trades, wins, avg_margin_pct FROM learning "
                      "ORDER BY trades DESC LIMIT ?", (args.n,)).fetchall()
    for item, trades, wins, avg in rows:
        print(f"  {item:28} {wins}/{trades} wins  avg margin {avg:.1%}")


def cmd_train(cfg, args):
    """Backtest + (re)train the win-probability model from bazaar.db."""
    print(train(Store(cfg.data_dir / "bazaar.db"), cfg))


def cmd_analyze(cfg, args):
    """Ask Claude to review the current candidates (uses the Anthropic API; costs tokens)."""
    from .analyst import Analyst
    agent = BazaarAgent(cfg)
    while True:
        try:
            now = time.time()
            quotes = agent.perceive()
            agent.update(quotes, now)
            picks = agent.decide(quotes, now)
            if not picks:
                print("No candidates pass the filters yet (history is still warming up).")
            else:
                text = Analyst(agent, picks).run()
                print(f"\n{text}\n")
                if args.discord:
                    agent.discord.send("analysis", f"**Bazaar analyst**\n{text}"[:1900])
        except BazaarError as e:
            logging.error("%s", e)
        except RuntimeError as e:
            sys.exit(str(e))
        if not args.every:
            return
        try:
            time.sleep(args.every * 60)
        except KeyboardInterrupt:
            return


def cmd_alert(cfg, args):
    """Notify on Discord when an item's price crosses a threshold."""
    name = args.name.strip().upper()
    discord = Discord(cfg.discord_webhook, args.cooldown)
    side = "ask" if args.side == "ask" else "bid"
    while True:
        try:
            q = fetch_quotes(cfg.api_key).get(name)
            price = getattr(q, side) if q else None
            hit = price is not None and (price > args.above if args.above is not None else price < args.below)
            if hit:
                text = f"**Bazaar price alert** `{name}` {side} = {price:,.1f}"
                print(text, "(sent)" if discord.send(name, text) else "(not sent)")
            time.sleep(args.interval)
        except BazaarError as e:
            logging.error("%s", e)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return


def main():
    p = argparse.ArgumentParser(prog="bazaar_agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the flipping agent")
    r.add_argument("--once", action="store_true", help="single cycle then exit")
    r.set_defaults(fn=cmd_run)
    t = sub.add_parser("top", help="top raw margins right now")
    t.add_argument("-n", type=int, default=10)
    t.set_defaults(fn=cmd_top)
    i = sub.add_parser("item", help="show one product")
    i.add_argument("name")
    i.set_defaults(fn=cmd_item)
    tr = sub.add_parser("train", help="backtest and retrain the win-probability model")
    tr.set_defaults(fn=cmd_train)
    an = sub.add_parser("analyze", help="Claude reviews the candidates with tools (costs API tokens)")
    an.add_argument("--every", type=int, default=0, help="repeat every N minutes (default: once)")
    an.add_argument("--discord", action="store_true", help="also post the analysis to Discord")
    an.set_defaults(fn=cmd_analyze)
    st = sub.add_parser("stats", help="database size and recommendation outcomes")
    st.add_argument("-n", type=int, default=10)
    st.set_defaults(fn=cmd_stats)
    a = sub.add_parser("alert", help="Discord alert when a price crosses a threshold")
    a.add_argument("name")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--above", type=float)
    g.add_argument("--below", type=float)
    a.add_argument("--side", choices=["ask", "bid"], default="ask",
                   help="ask = sell-offer price (default), bid = buy-order price")
    a.add_argument("--interval", type=int, default=180)
    a.add_argument("--cooldown", type=int, default=3600)
    a.set_defaults(fn=cmd_alert)

    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        args.fn(Config.from_env(), args)
    except BazaarError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
