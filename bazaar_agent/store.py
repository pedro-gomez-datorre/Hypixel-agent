"""SQLite persistence: price snapshots, recommendations, pending outcomes and learning stats."""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ts REAL NOT NULL, item TEXT NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
    buy_flow REAL NOT NULL, sell_flow REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_item_ts ON prices(item, ts);
CREATE TABLE IF NOT EXISTS recommendations (
    ts REAL NOT NULL, item TEXT NOT NULL, bid REAL, ask REAL, qty REAL, ppu REAL,
    margin_pct REAL, profit_day REAL, risk REAL, confidence REAL
);
CREATE TABLE IF NOT EXISTS pending (
    item TEXT PRIMARY KEY, made_at REAL NOT NULL, margin_pct REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS learning (
    item TEXT PRIMARY KEY, trades INTEGER, wins INTEGER, losses INTEGER, avg_margin_pct REAL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item TEXT NOT NULL, qty INTEGER NOT NULL,
    buy_price REAL NOT NULL, opened_at REAL NOT NULL, filled_at REAL,
    sell_price REAL, listed_at REAL, sold_at REAL, sold_price REAL
);
"""


class Store:
    def __init__(self, path: Path | str, readonly: bool = False):
        if readonly:  # the dashboard must never write or lock out the running agent
            if not Path(path).is_file():
                raise FileNotFoundError(f"{path} does not exist yet: start the agent first (run)")
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
            return
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        have = {r[1] for r in self.db.execute("PRAGMA table_info(prices)")}
        have_t = {r[1] for r in self.db.execute("PRAGMA table_info(trades)")}
        with self.db:
            if "cancelled_at" not in have_t:
                self.db.execute("ALTER TABLE trades ADD COLUMN cancelled_at REAL")
            for col in ("bid_amt", "bid_orders", "ask_amt", "ask_orders"):
                if col not in have:
                    self.db.execute(f"ALTER TABLE prices ADD COLUMN {col} REAL")

    def close(self) -> None:
        self.db.close()

    # prices
    def add_prices(self, ts: float, rows) -> None:
        """rows: (item, bid, ask, buy_flow, sell_flow[, bid_amt, bid_orders, ask_amt, ask_orders])."""
        padded = [(ts, *r, *([None] * (9 - len(r)))) for r in rows]
        with self.db:
            self.db.executemany(
                "INSERT INTO prices (ts, item, bid, ask, buy_flow, sell_flow, bid_amt, bid_orders, ask_amt, ask_orders) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)", padded)

    def recent_prices(self, item: str, limit: int) -> list[tuple[float, float, float]]:
        """Last ``limit`` (ts, bid, ask) samples for ``item``, oldest first."""
        rows = self.db.execute(
            "SELECT ts, bid, ask FROM prices WHERE item=? ORDER BY ts DESC LIMIT ?",
            (item, limit)).fetchall()
        return rows[::-1]

    def items_with_prices(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT item FROM prices")]

    # recommendations
    def add_recommendation(self, ts: float, item: str, *values) -> None:
        with self.db:
            self.db.execute("INSERT INTO recommendations VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (ts, item, *values))

    # pending outcomes
    def load_pending(self) -> dict[str, tuple[float, float]]:
        return {i: (t, m) for i, t, m in self.db.execute("SELECT item, made_at, margin_pct FROM pending")}

    def save_pending(self, item: str, made_at: float, margin_pct: float) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO pending VALUES (?,?,?)", (item, made_at, margin_pct))

    def delete_pending(self, item: str) -> None:
        with self.db:
            self.db.execute("DELETE FROM pending WHERE item=?", (item,))

    # learning
    def load_learning(self) -> dict[str, tuple[int, int, int, float]]:
        return {r[0]: r[1:] for r in self.db.execute(
            "SELECT item, trades, wins, losses, avg_margin_pct FROM learning")}

    def save_learning(self, item: str, trades: int, wins: int, losses: int, avg: float) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO learning VALUES (?,?,?,?,?)",
                            (item, trades, wins, losses, avg))
