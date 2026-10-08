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


def rows(store: Store, tax: float) -> list[dict]:
    """Every trade with its stage and, once sold, net profit after tax."""
    out = []
    for (tid, item, qty, buy, opened, filled, listed, sold,
         sold_price, sell_price) in store.db.execute(
            "SELECT id, item, qty, buy_price, opened_at, filled_at, listed_at, sold_at, sold_price, sell_price "
            "FROM trades ORDER BY id"):
        d = {"id": tid, "item": item, "qty": qty, "buy_price": buy, "sell_price": sold_price or sell_price,
             "stage": "sold" if sold else "listed" if listed else "filled" if filled else "buy order open",
             "opened_at": opened, "fill_min": (filled - opened) / 60 if filled else None,
             "sell_min": (sold - listed) / 60 if sold and listed else None,
             "cost": buy * qty, "profit": None, "roi": None}
        if sold:
            d["profit"] = d["sell_price"] * qty * (1 - tax) - d["cost"]
            d["roi"] = d["profit"] / d["cost"]
        out.append(d)
    return out


def summary(store: Store, tax: float) -> str:
    rs = rows(store, tax)
    if not rs:
        return "No trades logged yet."
    lines = []
    for r in rs:
        if r["profit"] is not None:
            lines.append(f"  {r['item']:24} x{r['qty']:<6} buy {r['buy_price']:,.1f} -> sold {r['sell_price']:,.1f}  "
                         f"profit {r['profit']:>10,.0f} ({r['roi']:+.0%})  fill {r['fill_min']:.0f}m  sell {r['sell_min']:.0f}m")
        else:
            lines.append(f"  {r['item']:24} x{r['qty']:<6} buy {r['buy_price']:,.1f}  [{r['stage']}]")
    done = [r for r in rs if r["profit"] is not None]
    if done:
        n, total, cost = len(done), sum(r["profit"] for r in done), sum(r["cost"] for r in done)
        lines.append(f"\n{n} completed: profit {total:,.0f} on {cost:,.0f} invested ({total / cost:+.0%}), "
                     f"avg fill {sum(r['fill_min'] for r in done) / n:.0f} min, "
                     f"avg sell {sum(r['sell_min'] for r in done) / n:.0f} min")
    return "\n".join(lines)
