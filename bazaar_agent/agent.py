"""The agent loop: perceive -> update -> decide -> act."""
import dataclasses
import logging
import time
from collections import defaultdict, deque
from datetime import datetime

from .config import Config
from .dataset import make_features
from .market import BazaarError, Quote, fetch_quotes
from .memory import Memory, import_learning_csv
from .model import Predictor, train
from .notify import Discord
from .store import Store
from .strategy import Pick, evaluate, margin_per_unit

log = logging.getLogger(__name__)


class BazaarAgent:
    def __init__(self, cfg: Config, store: Store | None = None):
        self.cfg = cfg
        self.store = store or Store(cfg.data_dir / "bazaar.db")
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=cfg.history_len))
        for item in self.store.items_with_prices():  # warm start from stored snapshots
            self.history[item].extend(
                (ts, bid, ask) for ts, bid, ask in self.store.recent_prices(item, cfg.history_len))
        import_learning_csv(cfg.data_dir / "learning.csv", self.store)
        self.memory = Memory(self.store, cfg.outcome_horizon, cfg.min_margin)
        self.discord = Discord(cfg.discord_webhook, cfg.alert_cooldown)
        self.predictor = Predictor.load(cfg)
        self._last_logged: dict[str, float] = {}
        self._last_train = 0.0
        self._last_snapshot = max((h[-1][0] for h in self.history.values() if h), default=0.0)

    def perceive(self) -> dict[str, Quote]:
        return fetch_quotes(self.cfg.api_key)

    def update(self, quotes: dict[str, Quote], now: float) -> None:
        if now - self._last_snapshot >= self.cfg.snapshot_interval:
            self._last_snapshot = now
            rows = []
            for name, q in quotes.items():
                self.history[name].append((now, q.bid, q.ask))
                rows.append((name, q.bid, q.ask, q.buy_flow, q.sell_flow,
                             q.bid_amt, q.bid_orders, q.ask_amt, q.ask_orders))
            self.store.add_prices(now, rows)  # every product, so any item can be charted
        current = {n: (margin_per_unit(q, self.cfg.tax) / q.bid if q.bid else None) for n, q in quotes.items()}
        self.memory.resolve(now, current)

    def decide(self, quotes: dict[str, Quote], now: float | None = None) -> list[Pick]:
        now = now or time.time()
        picks = []
        for name, q in quotes.items():
            p = evaluate(q, self.history[name], self.memory.get(name).fail_rate, self.cfg)
            if not p:
                continue
            if self.predictor:
                x = make_features(q, self.history[name], now, self.cfg)
                if x:
                    pw = self.predictor.p_win(x)
                    p = dataclasses.replace(p, p_win=pw, score=p.score * pw)
            picks.append(p)
        return sorted(picks, key=lambda p: p.score, reverse=True)[: self.cfg.top_n]

    def maybe_retrain(self, now: float) -> None:
        """Retrain periodically; the new model is only used if it beats the heuristic."""
        if now - self._last_train < self.cfg.retrain_interval:
            return
        self._last_train = now
        try:
            report = train(self.store, self.cfg)
            log.info("retrain:\n%s", report)
            self.predictor = Predictor.load(self.cfg)
        except Exception:
            log.exception("retraining failed; keeping the previous model")

    def act(self, picks: list[Pick], now: float) -> None:
        for p in picks:
            self.memory.watch(p.name, now, p.margin_pct)
            conf = self.memory.get(p.name).confidence
            if now - self._last_logged.get(p.name, 0) >= self.cfg.log_cooldown:
                self._log(now, p, conf)
                self._last_logged[p.name] = now
            if p.ppu >= self.cfg.alert_min_ppu and p.qty >= self.cfg.alert_min_qty:
                self.discord.send(p.name, (
                    f"**Bazaar Flip Alert** `{p.name}`\n"
                    f"Buy order {p.bid:,.1f} -> sell offer {p.ask:,.1f}\n"
                    f"Profit/unit {p.ppu:,.0f} ({p.margin_pct:.1%}) | ~{p.qty:,.0f} units/day | "
                    f"Profit/day {p.profit_day:,.0f} | Risk {p.risk:.0f}"))
        self._print(picks)

    def _log(self, now: float, p: Pick, conf: float) -> None:
        self.store.add_recommendation(now, p.name, p.bid, p.ask, p.qty, p.ppu, p.margin_pct,
                                      p.profit_day, p.risk, conf)

    def _print(self, picks: list[Pick]) -> None:
        print(f"\n{datetime.now():%H:%M:%S}  top {len(picks)} flips "
              f"({len(self.memory.pending)} pending outcomes)")
        if not picks:
            print("  (nothing passes the filters yet; warming up price history)")
        for p in picks:
            conf = self.memory.get(p.name).confidence
            print(f"  {p.name:28} margin {p.margin_pct:6.1%}  ppu {p.ppu:>11,.0f}  "
                  f"qty/day {p.qty:>8,.0f}  profit/day {p.profit_day:>13,.0f}  "
                  f"risk {p.risk:3.0f}  conf {conf:.2f}"
                  + (f"  p(win) {p.p_win:.2f}" if p.p_win is not None else ""))

    def step(self) -> list[Pick]:
        now = time.time()
        quotes = self.perceive()
        self.update(quotes, now)
        picks = self.decide(quotes, now)
        self.act(picks, now)
        self.maybe_retrain(now)
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
