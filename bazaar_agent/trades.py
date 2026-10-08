"""Manual log of real flips: ground truth for how long fills take and what you actually earn.

Lifecycle of a trade:  open (buy order placed) -> filled -> listed (sell offer placed) -> sold.
Each step stamps the current time; ``ago`` backdates it by that many minutes.
"""
import time

from .store import Store

STEPS = ("opened_at", "filled_at", "listed_at", "sold_at")


def _now(ago: float) -> float:
    return time.time() - ago * 60


def active(store: Store, item: str):
    """The unfinished trade for ``item`` (newest first), or None."""
    return store.db.execute(
        "SELECT id, opened_at, filled_at, listed_at, sold_at FROM trades "
        "WHERE item=? AND sold_at IS NULL AND cancelled_at IS NULL ORDER BY id DESC LIMIT 1", (item,)).fetchone()


def open_trade(store: Store, item: str, qty: int, price: float, ago: float = 0) -> int:
    with store.db:
        cur = store.db.execute(
            "INSERT INTO trades (item, qty, buy_price, opened_at) VALUES (?,?,?,?)",
            (item, qty, price, _now(ago)))
    return cur.lastrowid


def _update(store: Store, item: str, require: str, sets: str, args: tuple) -> int:
    row = active(store, item)
    if not row:
        raise ValueError(f"no open trade for {item}; start one with: trade open {item} --price P --qty N")
    tid, opened, filled, listed, sold = row
    state = {"opened_at": opened, "filled_at": filled, "listed_at": listed}
    if state[require] is None:
        raise ValueError(f"{item}: that step needs '{require.replace('_at', '')}' first")
    with store.db:
        store.db.execute(f"UPDATE trades SET {sets} WHERE id=?", (*args, tid))
    return tid


def mark_filled(store: Store, item: str, ago: float = 0) -> int:
    return _update(store, item, "opened_at", "filled_at=?", (_now(ago),))


def mark_listed(store: Store, item: str, price: float, ago: float = 0) -> int:
    return _update(store, item, "filled_at", "sell_price=?, listed_at=?", (price, _now(ago)))


def mark_sold(store: Store, item: str, price: float | None = None, ago: float = 0) -> int:
    tid = _update(store, item, "listed_at", "sold_at=?", (_now(ago),))
    with store.db:
        store.db.execute("UPDATE trades SET sold_price = COALESCE(?, sell_price) WHERE id=?", (price, tid))
    return tid


def done_trade(store: Store, item: str, qty: int, buy: float, sell: float, fill_min: float, sell_min: float) -> int:
    """Log a finished flip in one go. ``fill_min`` / ``sell_min`` are how long each leg took."""
    now = time.time()
    opened = now - (fill_min + sell_min) * 60
    filled = now - sell_min * 60
    with store.db:
        cur = store.db.execute(
            "INSERT INTO trades (item, qty, buy_price, opened_at, filled_at, sell_price, listed_at, sold_at, sold_price) "
            "VALUES (?,?,?,?,?,?,?,?,?)", (item, qty, buy, opened, filled, sell, filled, now, sell))
    return cur.lastrowid


def mark_cancelled(store: Store, item: str, ago: float = 0) -> int:
    """Close an unfinished trade that you gave up on (e.g. a sell offer that never sold)."""
    return _update(store, item, "opened_at", "cancelled_at=?", (_now(ago),))


def _has_column(store: Store, table: str, col: str) -> bool:
    return any(r[1] == col for r in store.db.execute(f"PRAGMA table_info({table})"))


def _predicted_margin(store: Store, item: str, ts: float, tax: float) -> float | None:
    """Net margin the agent saw for ``item`` in the last snapshot at or before ``ts``."""
    row = store.db.execute("SELECT bid, ask FROM prices WHERE item=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                           (item, ts)).fetchone()
    return (row[1] * (1 - tax) - row[0]) / row[0] if row and row[0] else None


def rows(store: Store, tax: float) -> list[dict]:
    """Every trade with its stage and, once sold, net profit after tax and how it compares to the prediction."""
    cancelled = "cancelled_at" if _has_column(store, "trades", "cancelled_at") else "NULL"
    out = []
    for (tid, item, qty, buy, opened, filled, listed, sold, sold_price, sell_price, canc) in store.db.execute(
            "SELECT id, item, qty, buy_price, opened_at, filled_at, listed_at, sold_at, sold_price, sell_price, "
            f"{cancelled} FROM trades ORDER BY id"):
        stage = ("sold" if sold else "cancelled" if canc else "listed" if listed
                 else "filled" if filled else "buy order open")
        d = {"id": tid, "item": item, "qty": qty, "buy_price": buy, "sell_price": sold_price or sell_price,
             "stage": stage, "opened_at": opened,
             "fill_min": (filled - opened) / 60 if filled else None,
             "sell_min": (sold - listed) / 60 if sold and listed else None,
             "wait_min": (canc - listed) / 60 if canc and listed else None,   # cancelled after waiting this long
             "cost": buy * qty, "profit": None, "roi": None, "pred_margin": None, "realization": None}
        if sold:
            d["profit"] = d["sell_price"] * qty * (1 - tax) - d["cost"]
            d["roi"] = d["profit"] / d["cost"]
            d["pred_margin"] = _predicted_margin(store, item, opened, tax)
            if d["pred_margin"]:
                d["realization"] = d["roi"] / d["pred_margin"]
        out.append(d)
    return out


def realization(store: Store, tax: float, min_trades: int = 3) -> tuple[float, int] | None:
    """Median of (realized return / predicted margin) over finished trades, or None with too few trades."""
    vals = sorted(r["realization"] for r in rows(store, tax) if r["realization"] is not None)
    if len(vals) < min_trades:
        return None
    mid = len(vals) // 2
    return (vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2), len(vals)


def summary(store: Store, tax: float) -> str:
    rs = rows(store, tax)
    if not rs:
        return "No trades logged yet."
    lines = []
    for r in rs:
        if r["profit"] is not None:
            extra = f"  vs predicted {r['pred_margin']:+.0%}" if r["pred_margin"] else ""
            lines.append(f"  {r['item']:24} x{r['qty']:<6} buy {r['buy_price']:,.1f} -> sold {r['sell_price']:,.1f}  "
                         f"profit {r['profit']:>10,.0f} ({r['roi']:+.0%}{extra})  fill {r['fill_min']:.0f}m  sell {r['sell_min']:.0f}m")
        elif r["stage"] == "cancelled":
            wait = f" after waiting {r['wait_min']:.0f} min to sell" if r["wait_min"] is not None else ""
            lines.append(f"  {r['item']:24} x{r['qty']:<6} buy {r['buy_price']:,.1f}  [cancelled{wait}]")
        else:
            lines.append(f"  {r['item']:24} x{r['qty']:<6} buy {r['buy_price']:,.1f}  [{r['stage']}]")
    done = [r for r in rs if r["profit"] is not None]
    gave_up = sum(r["stage"] == "cancelled" for r in rs)
    if done:
        n, total, cost = len(done), sum(r["profit"] for r in done), sum(r["cost"] for r in done)
        lines.append(f"\n{n} completed" + (f", {gave_up} cancelled" if gave_up else "") +
                     f": profit {total:,.0f} on {cost:,.0f} invested ({total / cost:+.0%}), "
                     f"avg fill {sum(r['fill_min'] for r in done) / n:.0f} min, "
                     f"avg sell {sum(r['sell_min'] for r in done) / n:.0f} min")
    real = realization(store, tax)
    if real:
        lines.append(f"You realize about {real[0]:.0%} of the predicted margin (median of {real[1]} trades).")
    return "\n".join(lines)
