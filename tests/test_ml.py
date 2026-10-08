import random
from types import SimpleNamespace as NS

from bazaar_agent.agent import BazaarAgent
from bazaar_agent.analyst import Analyst
from bazaar_agent.config import Config
from bazaar_agent.dataset import FEATURES, build, make_features
from bazaar_agent.market import Quote
from bazaar_agent.model import Predictor, train
from bazaar_agent.store import Store
from bazaar_agent.strategy import evaluate

H = 3600
CFG = Config(label_horizon=H, min_train_rows=50, min_samples=1)


def fill(store, items=30, steps=60, seed=1, persist=lambda i: True):
    rnd = random.Random(seed)
    for k in range(steps):
        rows = []
        for i in range(items):
            if k > 0 and not persist(i) and k % 20 > 9:
                continue  # item drops out of the plausible set
            bid = 1000 + i * 10
            rows.append((f"I{i}", bid, bid * (1.05 + rnd.random() * 0.05), 5000, 5000))
        store.add_prices(k * 300.0, rows)


def test_features_none_when_not_a_flip():
    q = Quote("X", 1000, 1000, 5000, 5000)
    assert make_features(q, [], 0.0, CFG) is None
    good = make_features(Quote("X", 1000, 1100, 5000, 5000), [], 0.0, CFG)
    assert len(good) == len(FEATURES)


def test_dataset_labels_win_and_dropout():
    s = Store(":memory:")
    for k in range(40):  # I0 persists, I1 vanishes after step 12 while the agent keeps recording
        rows = [("I0", 1000, 1100, 5000, 5000)]
        if k <= 12:
            rows.append(("I1", 1000, 1100, 5000, 5000))
        s.add_prices(k * 300.0, rows)
    ds = build(s, CFG)
    assert len(ds) > 0
    assert 0 in ds.y and 1 in ds.y
    assert ds.ts == sorted(ds.ts)


def test_unlabelled_when_agent_was_off():
    s = Store(":memory:")
    s.add_prices(0.0, [("I0", 1000, 1100, 5000, 5000)])  # nothing recorded an hour later
    assert len(build(s, CFG)) == 0


def test_train_reports_not_enough_data_and_does_not_adopt(tmp_path):
    s = Store(":memory:")
    fill(s, items=2, steps=5)
    r = train(s, CFG, tmp_path / "m.joblib")
    assert not r.adopted and "need" in r.reason


def test_train_never_adopts_pure_noise(tmp_path):
    s = Store(":memory:")
    fill(s, persist=lambda i: random.Random(i).random() > 0.5)
    r = train(s, Config(label_horizon=H, min_train_rows=50), tmp_path / "m.joblib")
    assert r.rows > 50 and not r.adopted
    assert Predictor.load(CFG, tmp_path / "m.joblib") is None  # unadopted model is ignored


class FakeClient:
    """Asks for one tool, then answers."""
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        if self.calls == 1:
            return NS(stop_reason="tool_use", content=[
                NS(type="tool_use", id="t1", name="get_candidates", input={}),
                NS(type="tool_use", id="t2", name="get_item_history", input={"item": "NOPE"}),
                NS(type="tool_use", id="t3", name="bogus", input={})])
        tool_msg = kw["messages"][-1]["content"]
        assert [r["tool_use_id"] for r in tool_msg] == ["t1", "t2", "t3"]  # all results in one message
        assert tool_msg[2].get("is_error")                                  # unknown tool -> error result
        return NS(stop_reason="end_turn", content=[NS(type="text", text="FLIP I0")])


def test_analyst_loop_runs_tools_and_returns_text(tmp_path):
    agent = BazaarAgent(Config(data_dir=tmp_path, min_samples=1))
    q = Quote("I0", 1000, 1100, 5000, 5000)
    picks = [evaluate(q, [(0, 1000, 1100)], 0.0, agent.cfg)]
    assert picks[0]
    assert Analyst(agent, picks, client=FakeClient()).run() == "FLIP I0"


def test_report_flags_spikes_and_short_history(tmp_path):
    from bazaar_agent.report import review
    agent = BazaarAgent(Config(data_dir=tmp_path, min_samples=1))
    for k in range(10):  # usual margin ~5%, then a spike
        agent.history["S"].append((k, 1000, 1070))
    agent.history["S"].append((10, 1000, 1300))
    spike = evaluate(Quote("S", 1000, 1300, 5000, 5000), agent.history["S"], 0.0, agent.cfg)
    assert review(agent, spike)[0] == "skip"
    agent.history["N"].append((0, 1000, 1100))
    new = evaluate(Quote("N", 1000, 1100, 5000, 5000), agent.history["N"], 0.0, agent.cfg)
    assert review(agent, new)[0] == "watch"


def test_dashboard_data_functions(tmp_path):
    import time
    from bazaar_agent import trades, ui
    from bazaar_agent.store import Store
    cfg = Config(data_dir=tmp_path, min_samples=1)
    s = Store(tmp_path / "bazaar.db")
    now = time.time()
    for k in range(6):
        s.add_prices(now - (5 - k) * 300, [("X", 1000, 1300, 5000, 5000), ("THIN", 1000, 1005, 5000, 5000)])
    trades.open_trade(s, "X", 2, 1000, ago=30); trades.mark_filled(s, "X", ago=25)
    trades.mark_listed(s, "X", 1300, ago=24); trades.mark_sold(s, "X")
    s.close()
    ro = ui.open_store(cfg)
    ov = ui.overview(cfg, ro)
    assert ov["trades_done"] == 1 and ov["profit"] > 0 and ov["model"] is None
    cands = ui.candidates(cfg, ro)
    assert [c["name"] for c in cands["items"]] == ["X"]          # THIN fails the margin filter
    assert cands["items"][0]["verdict"] in {"flip", "watch", "skip"}
    h = ui.item_history(cfg, ro, "X", 24)
    assert len(h["t"]) == 6 and h["margin"][0] > 0
    assert ui.item_history(cfg, ro, "NOPE", 24)["t"] == []
    import pytest
    with pytest.raises(Exception):
        ro.db.execute("INSERT INTO pending VALUES ('a',1,1)")      # read-only: the dashboard can't write
    assert not ui.ITEM_RE.match("../etc") and ui.ITEM_RE.match("SAND:1")
