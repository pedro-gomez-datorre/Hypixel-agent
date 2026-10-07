"""The agent loop: perceive -> update -> decide -> act."""
import csv
import logging
import time
from collections import defaultdict, deque
from datetime import datetime

from .config import Config
from .market import BazaarError, Quote, fetch_quotes
from .memory import Memory
from .notify import Discord
from .strategy import Pick, evaluate, margin_per_unit

log = logging.getLogger(__name__)
LOG_FIELDS = ["time", "item", "bid", "ask", "qty", "ppu", "margin_pct", "profit_day", "risk", "confidence"]


class BazaarAgent:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=cfg.history_len))
        self.memory = Memory(cfg.data_dir / "learning.csv", cfg.outcome_horizon, cfg.min_margin)
        self.discord = Discord(cfg.discord_webhook, cfg.alert_cooldown)
        self.log_path = cfg.data_dir / "recommendations.csv"
        self._last_logged: dict[str, float] = {}

    def perceive(self) -> dict[str, Quote]:
        return fetch_quotes(self.cfg.api_key)

    def update(self, quotes: dict[str, Quote], now: float) -> None:
        for name, q in quotes.items():
            self.history[name].append((now, q.bid, q.ask))
        current = {n: (margin_per_unit(q, self.cfg.tax) / q.bid if q.bid else None) for n, q in quotes.items()}
        self.memory.resolve(now, current)

    def decide(self, quotes: dict[str, Quote]) -> list[Pick]:
        picks = []
        for name, q in quotes.items():
            p = evaluate(q, self.history[name], self.memory.get(name).fail_rate, self.cfg)
            if p:
                picks.append(p)
        return sorted(picks, key=lambda p: p.score, reverse=True)[: self.cfg.top_n]

    def act(self, picks: list[Pick], now: float) -> None:
        for p in picks:
            self.memory.watch(p.name, now, p.margin_pct)
            conf = self.memory.get(p.name).confidence
            if now - self._last_logged.get(p.name, 0) >= self.cfg.log_cooldown:
                self._log(p, conf)
                self._last_logged[p.name] = now
            if p.ppu >= self.cfg.alert_min_ppu and p.qty >= self.cfg.alert_min_qty:
                self.discord.send(p.name, (
                    f"**Bazaar Flip Alert** `{p.name}`\n"
                    f"Buy order {p.bid:,.1f} -> sell offer {p.ask:,.1f}\n"
                    f"Profit/unit {p.ppu:,.0f} ({p.margin_pct:.1%}) | ~{p.qty:,.0f} units/day | "
                    f"Profit/day {p.profit_day:,.0f} | Risk {p.risk:.0f}"))
        self._print(picks)

    def _log(self, p: Pick, conf: float) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.log_path.is_file()
        with self.log_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_FIELDS)
            w.writerow([datetime.now().isoformat(timespec="seconds"), p.name, p.bid, p.ask,
                        int(p.qty), round(p.ppu, 2), round(p.margin_pct, 4),
                        int(p.profit_day), int(p.risk), round(conf, 2)])

    def _print(self, picks: list[Pick]) -> None:
        print(f"\n{datetime.now():%H:%M:%S}  top {len(picks)} flips "
              f"({len(self.memory.pending)} pending outcomes)")
        if not picks:
            print("  (nothing passes the filters yet; warming up price history)")
        for p in picks:
            conf = self.memory.get(p.name).confidence
            print(f"  {p.name:28} margin {p.margin_pct:6.1%}  ppu {p.ppu:>11,.0f}  "
                  f"qty/day {p.qty:>8,.0f}  profit/day {p.profit_day:>13,.0f}  "
                  f"risk {p.risk:3.0f}  conf {conf:.2f}")

    def step(self) -> list[Pick]:
        now = time.time()
        quotes = self.perceive()
        self.update(quotes, now)
        picks = self.decide(quotes)
        self.act(picks, now)
        return picks

    def run(self, once: bool = False) -> None:
        while True:
            try:
                self.step()
            except BazaarError as e:
                log.error("%s (retrying next cycle)", e)
            except KeyboardInterrupt:
                return
            except Exception:
                log.exception("unexpected error in cycle; continuing")
            if once:
                return
            try:
                time.sleep(self.cfg.poll_seconds)
            except KeyboardInterrupt:
                return
