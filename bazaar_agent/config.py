"""Settings, read from environment variables (or a local .env file)."""
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    return default if raw in (None, "") else cast(raw)


@dataclass(frozen=True)
class Config:
    api_key: str = ""                 # the bazaar endpoint is public; key is optional
    discord_webhook: str = ""
    purse: float = 50_000_000
    tax: float = 0.0125               # tax on the sell offer (lower with community upgrades)
    poll_seconds: int = 30
    top_n: int = 5
    capture: float = 0.10             # share of the market flow you realistically fill
    min_daily_volume: float = 1_000   # items/day on the slower side of the flip
    min_margin: float = 0.02          # minimum net margin (2%)
    max_margin: float = 0.50          # above this the spread is treated as manipulation
    max_invest_frac: float = 0.20     # max share of the purse in a single item
    snapshot_interval: int = 300      # seconds between stored price snapshots (also history step)
    history_len: int = 120            # snapshots kept per item in memory (~10h at 5 min)
    min_samples: int = 3              # samples needed before an item can be recommended
    outcome_horizon: int = 3600       # seconds until a recommendation is judged
    log_cooldown: int = 900           # seconds between log rows for the same item
    alert_cooldown: int = 3600        # seconds between Discord alerts for the same item
    alert_min_ppu: float = 100_000
    alert_min_qty: float = 5_000
    label_horizon: int = 3600         # seconds ahead when labelling training examples
    min_train_rows: int = 500         # below this the model is not trained
    retrain_interval: int = 86400     # seconds between automatic retrains in `run`
    min_auc_gain: float = 0.01        # model must beat the heuristic baseline by this much (AUC)
    analyst_model: str = "claude-opus-5-5"
    analyst_language: str = "español"
    data_dir: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Config":
        _load_dotenv()
        d = cls()
        return cls(
            api_key=_env("HYPIXEL_API_KEY", d.api_key),
            discord_webhook=_env("DISCORD_WEBHOOK_URL", d.discord_webhook),
            purse=_env("PURSE", d.purse, float),
            tax=_env("BAZAAR_TAX", d.tax, float),
            poll_seconds=_env("POLL_SECONDS", d.poll_seconds, int),
            top_n=_env("TOP_N", d.top_n, int),
            capture=_env("CAPTURE", d.capture, float),
            analyst_model=_env("ANALYST_MODEL", d.analyst_model),
            analyst_language=_env("ANALYST_LANGUAGE", d.analyst_language),
            data_dir=Path(_env("DATA_DIR", str(d.data_dir))),
        )
