"""Turn stored price snapshots into labelled training examples (also used as the backtest).

Example = one snapshot of one item that looked like a flip (margin and flow within the configured
bounds). Label = 1 if, ``label_horizon`` seconds later, the margin is still >= max(min_margin,
half the original). If the item is missing from later snapshots while *other* items were recorded,
it dropped out of the plausible-flip set, which is a loss. If nothing was recorded then (the agent
was off) the example is unlabelled and skipped.
"""
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .store import Store
from .strategy import features, risk_score, margin_per_unit
from .market import Quote

FEATURES = ["margin_pct", "log_flow", "flow_imbalance", "log_bid", "volatility", "trend",
            "hour_sin", "hour_cos", "samples", "baseline_score"]
TRAIL = 12  # trailing snapshots used for volatility/trend


def make_features(q: Quote, hist: deque, ts: float, cfg: Config) -> list[float] | None:
    """Feature vector for a quote, or None if it is not a plausible flip."""
    ppu = margin_per_unit(q, cfg.tax)
    if ppu <= 0 or q.bid <= 0:
        return None
    pct = ppu / q.bid
    flow = min(q.buy_flow, q.sell_flow)
    if not cfg.min_margin <= pct <= cfg.max_margin or flow < cfg.min_daily_volume:
        return None
    f = features(deque(list(hist)[-TRAIL:]))
    base = 1 - risk_score(pct, flow, f, 0.1, 0.0, cfg) / 100  # purse-independent heuristic
    hour = datetime.fromtimestamp(ts, timezone.utc).hour
    ratio = (q.buy_flow + 1) / (q.sell_flow + 1)
    return [pct, math.log1p(flow), math.log(ratio), math.log(q.bid), f.volatility, f.trend,
            math.sin(2 * math.pi * hour / 24), math.cos(2 * math.pi * hour / 24), f.samples, base]


@dataclass
class Dataset:
    X: list[list[float]]
    y: list[int]
    ts: list[float]

    def __len__(self) -> int:
        return len(self.y)


def build(store: Store, cfg: Config) -> Dataset:
    rows = store.db.execute(
        "SELECT ts, item, bid, ask, buy_flow, sell_flow FROM prices ORDER BY ts").fetchall()
    all_ts = sorted({r[0] for r in rows})
    by_item: dict[str, list] = defaultdict(list)
    for r in rows:
        by_item[r[1]].append(r)

    def recorded_between(lo: float, hi: float) -> bool:
        from bisect import bisect_left
        i = bisect_left(all_ts, lo)
        return i < len(all_ts) and all_ts[i] <= hi

    H = cfg.label_horizon
    X, y, T = [], [], []
    for item, snaps in by_item.items():
        hist: deque = deque(maxlen=TRAIL)
        for i, (ts, _, bid, ask, bf, sf) in enumerate(snaps):
            hist.append((ts, bid, ask))
            q = Quote(item, bid, ask, bf, sf)
            x = make_features(q, hist, ts, cfg)
            if x is None:
                continue
            target = next((s for s in snaps[i + 1:] if s[0] >= ts + H), None)
            if target is not None and target[0] <= ts + 2 * H:
                tq = Quote(item, target[2], target[3], target[4], target[5])
                now_pct = margin_per_unit(tq, cfg.tax) / tq.bid
                label = int(now_pct >= max(cfg.min_margin, x[0] / 2))
            elif recorded_between(ts + H, ts + 2 * H):
                label = 0  # dropped out of the plausible set while the agent was watching
            else:
                continue
            X.append(x); y.append(label); T.append(ts)
    order = sorted(range(len(T)), key=T.__getitem__)
    return Dataset([X[i] for i in order], [y[i] for i in order], [T[i] for i in order])
