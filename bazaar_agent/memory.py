"""Outcome tracking: did a recommended flip still look good after a while?

A recommendation is judged once, ``horizon`` seconds after it was made: it is a win if the net
margin is still at least ``min_margin`` and at least half of the margin at recommendation time
(the opportunity did not evaporate or get undercut). Only judged outcomes feed the statistics,
so repeated recommendations of the same item do not inflate them. State lives in the Store, so
pending recommendations survive restarts.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

from .store import Store


@dataclass
class Record:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    avg_margin_pct: float = 0.0

    @property
    def fail_rate(self) -> float:
        return self.losses / self.trades if self.trades >= 5 else 0.0

    @property
    def confidence(self) -> float:
        """Laplace-smoothed win rate: 0.5 with no data, converges to the observed rate."""
        return (self.wins + 1) / (self.trades + 2)


class Memory:
    def __init__(self, store: Store, horizon: float, min_margin: float):
        self.store, self.horizon, self.min_margin = store, horizon, min_margin
        self.records = {k: Record(*v) for k, v in store.load_learning().items()}
        self.pending = store.load_pending()  # item -> (made_at, margin_pct)

    def get(self, name: str) -> Record:
        return self.records.get(name, Record())

    def watch(self, name: str, now: float, margin_pct: float) -> None:
        if name not in self.pending:
            self.pending[name] = (now, margin_pct)
            self.store.save_pending(name, now, margin_pct)

    def resolve(self, now: float, current_margin: dict[str, float | None]) -> int:
        """Judge due recommendations. ``current_margin[name]`` is None if the item vanished."""
        done = 0
        for name, (made_at, entry) in list(self.pending.items()):
            if now - made_at < self.horizon:
                continue
            del self.pending[name]
            self.store.delete_pending(name)
            now_pct = current_margin.get(name)
            win = now_pct is not None and now_pct >= max(self.min_margin, entry / 2)
            rec = self.records.setdefault(name, Record())
            rec.trades += 1
            rec.wins += win
            rec.losses += not win
            rec.avg_margin_pct += (max(now_pct or 0.0, 0.0) - rec.avg_margin_pct) / rec.trades
            self.store.save_learning(name, rec.trades, rec.wins, rec.losses, rec.avg_margin_pct)
            done += 1
        return done


def import_learning_csv(path: Path, store: Store) -> int:
    """One-time import of the v2 ``learning.csv``. Skipped if the DB already has learning data."""
    if not path.is_file() or store.load_learning():
        return 0
    n = 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            store.save_learning(row["item"], int(row["trades"]), int(row["wins"]),
                                int(row["losses"]), float(row["avg_margin_pct"]))
            n += 1
    return n
