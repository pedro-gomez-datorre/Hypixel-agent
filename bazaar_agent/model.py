"""Win-probability model, retrained from the database and adopted only if it beats the heuristic."""
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .dataset import FEATURES, Dataset, build
from .store import Store

log = logging.getLogger(__name__)


def _need_sklearn():
    try:
        import joblib, numpy, sklearn  # noqa: F401
    except ImportError as e:
        raise RuntimeError("the ML part needs: pip install scikit-learn numpy joblib") from e


@dataclass
class Report:
    rows: int
    test_rows: int
    base_rate: float
    auc_model: float | None
    auc_baseline: float | None
    adopted: bool
    reason: str

    def __str__(self) -> str:
        f = lambda v: "n/a" if v is None else f"{v:.3f}"
        return (f"rows {self.rows} (test {self.test_rows}), base win rate {self.base_rate:.1%}\n"
                f"AUC model {f(self.auc_model)} vs heuristic {f(self.auc_baseline)}\n"
                f"{'ADOPTED' if self.adopted else 'not adopted'}: {self.reason}")


def train(store: Store, cfg: Config, path: Path | None = None) -> Report:
    """Train on the older 75% of the data, validate on the newest 25% (no look-ahead)."""
    _need_sklearn()
    import joblib
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    ds: Dataset = build(store, cfg)
    n = len(ds)
    base = sum(ds.y) / n if n else 0.0
    if n < cfg.min_train_rows:
        return Report(n, 0, base, None, None, False,
                      f"only {n} labelled rows, need {cfg.min_train_rows}; keep the agent running")
    X, y = np.array(ds.X), np.array(ds.y)
    cut = int(n * 0.75)
    # purge: drop training rows whose label window overlaps the test period
    keep = [i for i in range(cut) if ds.ts[i] + cfg.label_horizon <= ds.ts[cut]]
    Xtr, ytr, Xte, yte = X[keep], y[keep], X[cut:], y[cut:]
    if len(set(ytr)) < 2 or len(set(yte)) < 2:
        return Report(n, len(yte), base, None, None, False, "train or test set has a single class")
    clf = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05,
                                         l2_regularization=1.0, random_state=0)
    clf.fit(Xtr, ytr)
    auc_m = float(roc_auc_score(yte, clf.predict_proba(Xte)[:, 1]))
    auc_b = float(roc_auc_score(yte, Xte[:, FEATURES.index("baseline_score")]))
    adopt = auc_m >= auc_b + cfg.min_auc_gain and len(yte) >= 200
    reason = ("beats the heuristic on newer, unseen data" if adopt else
              "does not beat the heuristic by the required margin" if len(yte) >= 200
              else "test set too small to trust")
    if adopt:  # final model uses all data
        clf.fit(X, y)
    p = path or cfg.data_dir / "model.joblib"
    p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": clf, "features": FEATURES, "adopted": adopt, "auc_model": auc_m,
                 "auc_baseline": auc_b, "rows": n, "trained_at": time.time()}, p)
    return Report(n, len(yte), base, auc_m, auc_b, adopt, reason)


class Predictor:
    """Loads the saved model; ``None`` from ``load`` means 'use the heuristic only'."""

    def __init__(self, blob: dict):
        self.model, self.meta = blob["model"], blob

    @classmethod
    def load(cls, cfg: Config, path: Path | None = None) -> "Predictor | None":
        p = path or cfg.data_dir / "model.joblib"
        if not p.is_file():
            return None
        try:
            _need_sklearn()
            import joblib
            blob = joblib.load(p)
        except Exception as e:  # corrupt file, missing sklearn, version mismatch
            log.warning("could not load model (%s); using heuristic only", e)
            return None
        return cls(blob) if blob.get("adopted") else None

    def p_win(self, x: list[float]) -> float:
        return float(self.model.predict_proba([x])[0][1])

    def summary(self) -> dict:
        m = self.meta
        return {"rows": m["rows"], "auc_model": round(m["auc_model"], 3),
                "auc_heuristic": round(m["auc_baseline"], 3),
                "trained_hours_ago": round((time.time() - m["trained_at"]) / 3600, 1)}
