"""CLI: python -m bazaar_agent {run,top,item,alert}"""
import argparse
import logging
import sys
import time

from .agent import BazaarAgent
from .config import Config
from .market import BazaarError, fetch_quotes
from .notify import Discord
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
