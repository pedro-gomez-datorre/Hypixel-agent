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
