"""Pure scoring logic: no I/O, easy to test."""
from collections import deque
from dataclasses import dataclass

from .config import Config
from .market import Quote


def margin_per_unit(q: Quote, tax: float) -> float:
    return q.ask * (1 - tax) - q.bid


def daily_quantity(q: Quote, cfg: Config) -> float:
    """Units/day you can realistically flip, bounded by market flow and by purse limits."""
    flow = min(q.buy_flow, q.sell_flow) * cfg.capture
    affordable = cfg.purse * cfg.max_invest_frac / q.bid if q.bid else 0
    return min(flow, affordable)


@dataclass(frozen=True)
class Features:
    volatility: float  # (max-min)/mean of the ask over the window
    trend: float       # relative ask change across the window
    samples: int


def features(history: deque) -> Features:
    asks = [a for _, _, a in history]
    if len(asks) < 2:
        return Features(0.0, 0.0, len(asks))
    mean = sum(asks) / len(asks)
    vol = (max(asks) - min(asks)) / mean if mean else 0.0
    trend = (asks[-1] - asks[0]) / asks[0] if asks[0] else 0.0
    return Features(vol, trend, len(asks))


def risk_score(margin_pct: float, daily_flow: float, f: Features,
               invest_frac: float, fail_rate: float, cfg: Config) -> float:
    """0 (safe) .. 100 (very risky). Weights sum to 1."""
    parts = {
        "profit": (0.25, max((cfg.min_margin - margin_pct) / cfg.min_margin, 0)),
        "liquidity": (0.25, max(1 - daily_flow / cfg.min_daily_volume, 0)),
        "volatility": (0.15, min(f.volatility / 0.05, 1)),
        "trend": (0.10, min(max(-f.trend / 0.05, 0), 1)),
        "invest": (0.10, min(invest_frac / cfg.max_invest_frac, 1)),
        "manipulation": (0.10, min(max(margin_pct - cfg.max_margin / 2, 0) / (cfg.max_margin / 2), 1)),
        "failures": (0.05, fail_rate),
    }
    return min(100.0, 100 * sum(w * v for w, v in parts.values()))


@dataclass(frozen=True)
class Pick:
    name: str
    bid: float
    ask: float
    ppu: float
    margin_pct: float
    qty: float
    profit_day: float
    risk: float
    score: float


def evaluate(q: Quote, hist: deque, fail_rate: float, cfg: Config) -> Pick | None:
    """Return a Pick if the quote passes every filter, else None."""
    ppu = margin_per_unit(q, cfg.tax)
    if ppu <= 0 or q.bid <= 0:
        return None
    margin_pct = ppu / q.bid
    if margin_pct < cfg.min_margin or margin_pct > cfg.max_margin:
        return None  # too thin, or so wide it is an illiquid/manipulated book
    flow = min(q.buy_flow, q.sell_flow)
    if flow < cfg.min_daily_volume:
        return None
    f = features(hist)
    if f.samples < cfg.min_samples:
        return None
    qty = daily_quantity(q, cfg)
    if qty <= 0:
        return None
    invest = min(qty * q.bid / cfg.purse, 1)
    risk = risk_score(margin_pct, flow, f, invest, fail_rate, cfg)
    profit_day = ppu * qty
    score = profit_day * (1 - risk / 100) ** 2
    return Pick(q.name, q.bid, q.ask, ppu, margin_pct, qty, profit_day, risk, score)
