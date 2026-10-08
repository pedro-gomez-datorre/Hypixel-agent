"""Local read-only dashboard: python -m bazaar_agent ui  ->  http://127.0.0.1:8765

Serves one HTML page plus a few JSON endpoints computed from data/bazaar.db. It never writes to the
database and listens on localhost only. The agent (`run`) keeps collecting data in another process.
"""
import dataclasses
import json
import re
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from . import trades
from .config import Config
from .dataset import make_features
from .market import Quote
from .memory import Memory
from .model import Predictor
from .report import review
from .store import Store
from .strategy import evaluate

PAGE = Path(__file__).with_name("ui.html")
ITEM_RE = re.compile(r"^[A-Z0-9_:]{1,80}$")
MAX_POINTS = 600


def open_store(cfg: Config) -> Store:
    return Store(cfg.data_dir / "bazaar.db", readonly=True)


def overview(cfg: Config, store: Store) -> dict:
    n_prices, first, last = store.db.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM prices").fetchone()
    n_recs = store.db.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
    judged, wins = store.db.execute(
        "SELECT COALESCE(SUM(trades),0), COALESCE(SUM(wins),0) FROM learning").fetchone()
    pred = Predictor.load(cfg)
    done = [r for r in trades.rows(store, cfg.tax) if r["profit"] is not None]
    cost = sum(r["cost"] for r in done)
    return {
        "now": time.time(), "last_snapshot": last,
        "data_hours": ((last - first) / 3600) if first else 0,
        "price_rows": n_prices, "recommendations": n_recs, "judged": judged, "judged_wins": wins,
        "model": pred.summary() if pred else None,
        "trades_done": len(done), "profit": sum(r["profit"] for r in done), "invested": cost,
        "avg_fill_min": sum(r["fill_min"] for r in done) / len(done) if done else None,
        "avg_sell_min": sum(r["sell_min"] for r in done) / len(done) if done else None,
    }


def candidates(cfg: Config, store: Store, limit: int = 60) -> dict:
    """Same filters, score and verdicts as `report`, computed from the latest stored snapshot."""
    last = store.db.execute("SELECT MAX(ts) FROM prices").fetchone()[0]
    if last is None:
        return {"ts": None, "items": []}
    pred = Predictor.load(cfg)
    memory = Memory(store, cfg.outcome_horizon, cfg.min_margin)
    history, picks = {}, []
    for item, bid, ask, bf, sf in store.db.execute(
            "SELECT item, bid, ask, buy_flow, sell_flow FROM prices WHERE ts=?", (last,)).fetchall():
        hist = deque(store.recent_prices(item, cfg.history_len), maxlen=cfg.history_len)
        q = Quote(item, bid, ask, bf, sf)
        p = evaluate(q, hist, memory.get(item).fail_rate, cfg)
        if not p:
            continue
        if pred:
            x = make_features(q, hist, last, cfg)
            if x:
                pw = pred.p_win(x)
                p = dataclasses.replace(p, p_win=pw, score=p.score * pw)
        history[item] = hist
        picks.append(p)
    picks.sort(key=lambda p: p.score, reverse=True)
    agent = SimpleNamespace(cfg=cfg, history=history, memory=memory, predictor=pred)
    items = []
    for p in picks[:limit]:
        verdict, notes = review(agent, p)
        rec = memory.get(p.name)
        items.append({"name": p.name, "bid": p.bid, "ask": p.ask, "ppu": p.ppu, "margin_pct": p.margin_pct,
                      "qty": p.qty, "profit_day": p.profit_day, "risk": p.risk, "p_win": p.p_win,
                      "verdict": verdict, "notes": notes, "judged": rec.trades, "wins": rec.wins})
    return {"ts": last, "total": len(picks), "items": items}


def item_history(cfg: Config, store: Store, item: str, hours: float) -> dict:
    last = store.db.execute("SELECT MAX(ts) FROM prices WHERE item=?", (item,)).fetchone()[0]
    if last is None:
        return {"item": item, "t": [], "bid": [], "ask": [], "margin": []}
    since = 0 if hours <= 0 else last - hours * 3600
    rows = store.db.execute(
        "SELECT ts, bid, ask FROM prices WHERE item=? AND ts>=? ORDER BY ts", (item, since)).fetchall()
    step = max(1, -(-len(rows) // MAX_POINTS))
    rows = rows[::step]
    return {"item": item, "t": [r[0] * 1000 for r in rows], "bid": [r[1] for r in rows],
            "ask": [r[2] for r in rows],
            "margin": [(r[2] * (1 - cfg.tax) - r[1]) / r[1] * 100 if r[1] else None for r in rows]}


class Handler(BaseHTTPRequestHandler):
    cfg: Config = Config()

    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(json.dumps(obj).encode(), "application/json", status)

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            return self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
        if url.path == "/favicon.ico":
            return self._send(b"", "image/x-icon", 204)
        try:
            store = open_store(self.cfg)
        except FileNotFoundError as e:
            return self._json({"error": str(e)}, 503)
        try:
            if url.path == "/api/overview":
                return self._json(overview(self.cfg, store))
            if url.path == "/api/candidates":
                return self._json(candidates(self.cfg, store))
            if url.path == "/api/trades":
                return self._json(trades.rows(store, self.cfg.tax))
            if url.path == "/api/history":
                item = (q.get("item") or [""])[0].upper()
                if not ITEM_RE.match(item):
                    return self._json({"error": "bad item"}, 400)
                try:
                    hours = float((q.get("hours") or ["24"])[0])
                except ValueError:
                    return self._json({"error": "bad hours"}, 400)
                return self._json(item_history(self.cfg, store, item, hours))
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # never take the dashboard down for one bad request
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            store.close()


def serve(cfg: Config, port: int = 8765, open_browser: bool = True) -> None:
    Handler.cfg = cfg
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Dashboard at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
