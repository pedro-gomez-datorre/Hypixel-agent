from collections import deque

from bazaar_agent.agent import BazaarAgent
from bazaar_agent.config import Config
from bazaar_agent.market import Quote, parse_quote
from bazaar_agent.memory import Memory
from bazaar_agent.store import Store
from bazaar_agent.strategy import evaluate, margin_per_unit

CFG = Config(min_samples=1)


def hist(*asks):
    return deque((i, 0, a) for i, a in enumerate(asks))


def q(bid=1000, ask=1100, flow=5000):
    return Quote("X", bid, ask, flow, flow)


def test_parse_quote_maps_inverted_api_names():
    item = {"sell_summary": [{"pricePerUnit": 90}], "buy_summary": [{"pricePerUnit": 110}],
            "quick_status": {"sellMovingWeek": 700, "buyMovingWeek": 1400}}
    quote = parse_quote("X", item)
    assert (quote.bid, quote.ask, quote.buy_flow, quote.sell_flow) == (90, 110, 100, 200)
    assert parse_quote("X", {"sell_summary": [], "buy_summary": []}) is None


def test_margin_includes_tax():
    assert margin_per_unit(q(1000, 1100), 0.0125) == 1100 * 0.9875 - 1000


def test_good_flip_is_picked():
    p = evaluate(q(), hist(1100, 1100), 0.0, CFG)
    assert p and p.ppu > 0 and 0 <= p.risk <= 100


def test_filters_reject_thin_illiquid_and_manipulated():
    assert evaluate(q(1000, 1010), hist(1010), 0.0, CFG) is None        # margin < 2%
    assert evaluate(q(flow=10), hist(1100), 0.0, CFG) is None           # illiquid
    assert evaluate(q(61_000, 444_000), hist(444_000), 0.0, CFG) is None  # 600% spread
    assert evaluate(q(), hist(), 0.0, Config(min_samples=3)) is None      # warming up


def test_quantity_capped_by_purse():
    cfg = Config(min_samples=1, purse=10_000, capture=1.0)
    p = evaluate(q(1000, 1100, flow=1_000_000), hist(1100), 0.0, cfg)
    assert p.qty * p.bid <= 10_000 * cfg.max_invest_frac + 1e-6


def test_memory_judges_once_after_horizon(tmp_path):
    db = tmp_path / "t.db"
    m = Memory(Store(db), horizon=100, min_margin=0.02)
    m.watch("A", 0, 0.10)
    m.watch("A", 50, 0.10)                      # duplicate must not reset or double count
    assert m.resolve(99, {"A": 0.10}) == 0
    assert m.resolve(100, {"A": 0.10}) == 1
    assert (m.get("A").trades, m.get("A").wins) == (1, 1)
    m.watch("B", 0, 0.10)
    m.resolve(200, {"B": 0.01})                 # margin collapsed -> loss
    assert m.get("B").losses == 1
    assert Memory(Store(db), 100, 0.02).get("A").wins == 1  # persisted


def test_pending_survives_restart(tmp_path):
    db = tmp_path / "t.db"
    Memory(Store(db), 100, 0.02).watch("A", 0, 0.10)
    m2 = Memory(Store(db), 100, 0.02)
    assert "A" in m2.pending and m2.resolve(150, {"A": 0.10}) == 1


def test_store_recent_prices_ordered_and_limited():
    s = Store(":memory:")
    for t in range(5):
        s.add_prices(t, [("X", 1, 2 + t, 10, 10)])
    assert [r[0] for r in s.recent_prices("X", 3)] == [2, 3, 4]


def test_agent_snapshots_warm_start_and_survives_errors(tmp_path, monkeypatch):
    cfg = Config(min_samples=1, data_dir=tmp_path, discord_webhook="http://127.0.0.1:1/x",
                 alert_min_ppu=0, alert_min_qty=0)
    agent = BazaarAgent(cfg)
    monkeypatch.setattr(agent, "perceive", lambda: {"X": q()})
    assert len(agent.step()) == 1                     # dead webhook must not raise
    assert agent.store.recent_prices("X", 10)         # snapshot stored
    assert agent.store.db.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] == 1
    agent.store.close()
    again = BazaarAgent(cfg)                          # restart: history comes from the DB
    assert len(again.history["X"]) == 1


def test_trade_lifecycle_and_profit(tmp_path):
    from bazaar_agent import trades
    s = Store(tmp_path / "t.db")
    trades.open_trade(s, "X", 64, 2000, ago=20)
    trades.mark_filled(s, "X", ago=15)
    trades.mark_listed(s, "X", 2900, ago=14)
    import pytest
    with pytest.raises(ValueError):
        trades.mark_filled(s, "NOPE")
    trades.mark_sold(s, "X", ago=0)
    out = trades.summary(s, 0.0125)
    profit = 64 * 2900 * 0.9875 - 64 * 2000
    assert f"{profit:,.0f}" in out and "1 completed" in out
    with pytest.raises(ValueError):  # steps must be in order
        trades.open_trade(s, "Y", 1, 1)
        trades.mark_listed(s, "Y", 5)
