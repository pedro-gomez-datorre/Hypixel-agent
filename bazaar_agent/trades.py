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
        "WHERE item=? AND sold_at IS NULL ORDER BY id DESC LIMIT 1", (item,)).fetchone()


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


def summary(store: Store, tax: float) -> str:
    rows = store.db.execute(
        "SELECT item, qty, buy_price, opened_at, filled_at, listed_at, sold_at, sold_price, sell_price "
        "FROM trades ORDER BY id").fetchall()
    if not rows:
        return "No trades logged yet."
    lines, done = [], []
    for item, qty, buy, opened, filled, listed, sold, sold_price, sell_price in rows:
        if sold:
            price = sold_price or sell_price
            cost, revenue = buy * qty, price * qty * (1 - tax)
            profit = revenue - cost
            fill_m, sell_m = (filled - opened) / 60, (sold - listed) / 60
            done.append((profit, cost, fill_m, sell_m))
            lines.append(f"  {item:24} x{qty:<6} buy {buy:,.1f} -> sold {price:,.1f}  "
                         f"profit {profit:>10,.0f} ({profit / cost:+.0%})  fill {fill_m:.0f}m  sell {sell_m:.0f}m")
        else:
            stage = ("listed" if listed else "filled" if filled else "buy order open")
            lines.append(f"  {item:24} x{qty:<6} buy {buy:,.1f}  [{stage}]")
    if done:
        n = len(done)
        total, cost = sum(d[0] for d in done), sum(d[1] for d in done)
        lines.append(f"\n{n} completed: profit {total:,.0f} on {cost:,.0f} invested ({total / cost:+.0%}), "
                     f"avg fill {sum(d[2] for d in done) / n:.0f} min, avg sell {sum(d[3] for d in done) / n:.0f} min")
    return "\n".join(lines)
