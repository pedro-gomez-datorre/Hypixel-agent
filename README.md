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
  still ≥ 2% and ≥ half of the original. Stats live in `data/learning.csv` and feed the risk score.

Data is written to `data/` (git-ignored). `legacy/` holds the CSVs from the first version; their
"learning" counters were not meaningful (every recommendation counted as a win).

## Tests
`python -m pytest`
