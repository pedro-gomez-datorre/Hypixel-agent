"""Outcome tracking: did a recommended flip still look good after a while?

A recommendation is judged once, ``horizon`` seconds after it was made: it is a win if the net
margin is still at least ``min_margin`` and at least half of the margin at recommendation time
(the opportunity did not evaporate or get undercut). Only judged outcomes feed the statistics,
so repeated recommendations of the same item do not inflate them.
"""
import csv
from dataclasses import dataclass
from pathlib import Path

FIELDS = ["item", "trades", "wins", "losses", "avg_margin_pct"]


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
    def __init__(self, path: Path, horizon: float, min_margin: float):
        self.path, self.horizon, self.min_margin = path, horizon, min_margin
        self.records: dict[str, Record] = {}
        self.pending: dict[str, tuple[float, float]] = {}  # item -> (made_at, margin_pct)
        self._load()

    def get(self, name: str) -> Record:
        return self.records.get(name, Record())

    def watch(self, name: str, now: float, margin_pct: float) -> None:
        self.pending.setdefault(name, (now, margin_pct))

    def resolve(self, now: float, current_margin: dict[str, float | None]) -> int:
        """Judge due recommendations. ``current_margin[name]`` is None if the item vanished."""
        done = 0
        for name, (made_at, entry) in list(self.pending.items()):
            if now - made_at < self.horizon:
                continue
            del self.pending[name]
            now_pct = current_margin.get(name)
            win = now_pct is not None and now_pct >= max(self.min_margin, entry / 2)
            rec = self.records.setdefault(name, Record())
            rec.trades += 1
            rec.wins += win
            rec.losses += not win
            rec.avg_margin_pct += (max(now_pct or 0.0, 0.0) - rec.avg_margin_pct) / rec.trades
            done += 1
        if done:
            self._save()
        return done

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                self.records[row["item"]] = Record(
                    int(row["trades"]), int(row["wins"]), int(row["losses"]),
                    float(row["avg_margin_pct"]))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(FIELDS)
            for k, r in sorted(self.records.items()):
                w.writerow([k, r.trades, r.wins, r.losses, round(r.avg_margin_pct, 4)])
        tmp.replace(self.path)
