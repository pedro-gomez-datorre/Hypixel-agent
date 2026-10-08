# Hypixel Bazaar Agent

Finds profitable **order flips** on the SkyBlock Bazaar (buy order → sell offer), ranks them by
expected profit per day adjusted for risk, and optionally pings Discord.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env        # optional: webhook, purse, tax...
python -m bazaar_agent run
```

| Command | What it does |
|---|---|
| `python -m bazaar_agent run [--once]` | Agent loop: recommendations, CSV log, Discord alerts |
| `python -m bazaar_agent stats` | Database size and how past recommendations turned out |
| `python -m bazaar_agent train` | Backtest + retrain the win-probability model from `bazaar.db` |
| `python -m bazaar_agent trade open ITEM --price P --qty N` | Log a real flip: then `filled`, `listed --price P`, `sold`, and `trade list` for results (`--ago MIN` backdates a step) |
| `python -m bazaar_agent ui` | Dashboard in your browser (read-only, localhost): candidates, look up any item (price, volume, live order book), your trades, model status |
| `python -m bazaar_agent report` | Rule-based review of the candidates: free, no API key |
| `python -m bazaar_agent analyze [--every 30] [--discord]` | Claude reviews the candidates with tools (costs API tokens) |
| `python -m bazaar_agent top -n 10` | Raw top margins right now (replaces `main.py` / `Order.py`) |
| `python -m bazaar_agent item ENCHANTED_CARROT` | Prices and flow of one product |
| `python -m bazaar_agent alert ITEM --above 13000` | Discord alert on price threshold (replaces `Exportable carrots.py`) |

## How it decides
* `bid` = highest buy order, `ask` = lowest sell offer. Net margin = `ask·(1−tax) − bid`.
* Quantity/day = `min(buy flow, sell flow) · capture`, capped by the share of your purse per item.
* Filters: margin between 2% and 50% (wider spreads are illiquid/manipulated books), minimum daily flow,
  and a few samples of price history before an item can be recommended.
* Risk (0–100): thin margin, low liquidity, volatility, downtrend, concentration, manipulation, past failures.
  Score = `profit/day · (1 − risk/100)²`.
* **Learning:** each recommendation is judged once after `outcome_horizon` (1 h): a win if the margin is
  still ≥ 2% and ≥ half of the original. Stats feed the risk score.

Everything is stored in one SQLite file, `data/bazaar.db` (git-ignored): price snapshots every 5 min
(every product, with best-level depth), recommendations, pending outcomes and learning stats. The agent warm-starts its
price history from it, so restarts lose nothing. An old `data/learning.csv` is imported once. `legacy/` holds the CSVs from the first version; their
"learning" counters were not meaningful (every recommendation counted as a win).

## Learning model
Every stored snapshot that looked like a flip is labelled by what happened an hour later (margin still
good = 1; margin gone or item dropped out of the plausible set = 0). A gradient-boosting model learns
`p(win)` from margin, flow, flow imbalance, volatility, trend and hour of day.
* Validation is **walk-forward**: train on the older 75%, test on the newest 25%, with the overlapping
  label window purged. The model is compared by AUC to the hand-written risk score.
* It is **adopted only if it beats the heuristic** (`min_auc_gain`) on enough test rows; otherwise the
  agent keeps using the heuristic. `run` retrains daily; `train` prints the comparison on demand.
* Needs data: ~500 labelled rows minimum (a day or two of `run`). The label is a proxy (the opportunity
  persists), not realised profit: fills cannot be observed.

## Claude analyst
`analyze` gives Claude read-only tools (candidates, price history, track record, model status) so it can
check candidates for manipulation or collapsing margins and write a short recommendation. It only advises.
It needs `ANTHROPIC_API_KEY` (or `ant auth login`) and costs tokens on every run (a few cents with the
default `claude-opus-5-5`; set `ANALYST_MODEL` to change it, `ANALYST_LANGUAGE` for the language).

## Tests
`python -m pytest`
