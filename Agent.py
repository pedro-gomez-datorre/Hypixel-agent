import requests
import time
import os
import csv
from datetime import datetime

API_KEY = "" #Put API here
URL = "https://api.hypixel.net/skyblock/bazaar"
TAX = 0.0125

LOG_FILE = "bazaar_log.csv"
LEARNING_FILE = "learning_file.csv"
DISCORD_WEBHOOK_URL = "" # connect discord here


def get_bazaar_data():
    try:
        r = requests.get(URL, params={"key": API_KEY}, timeout=10)
        r.raise_for_status()
        return r.json().get("products", {})
    except:
        return {}


def get_prices(item):
    sell = item.get("sell_summary", [])
    buy = item.get("buy_summary", [])
    return (
        sell[0]["pricePerUnit"] if sell else 0,
        buy[0]["pricePerUnit"] if buy else 0
    )


def get_daily_volumes(item):
    qs = item.get("quick_status", {})
    return qs.get("sellMovingWeek", 0) / 7, qs.get("buyMovingWeek", 0) / 7


def profit_per_unit(sell, buy):
    return buy * (1 - TAX) - sell


def risk_score(pp, volume, volatility, trend, invest_frac, fail_rate):
    profit_risk = max((0.02 - pp) / 0.02, 0)
    liquidity_risk = max((1 - volume / 1000), 0)
    volatility_risk = min(volatility / 0.05, 1)
    trend_risk = max(-trend / 0.05, 0)
    invest_risk = min(invest_frac / 0.2, 1)

    score = (
        0.3 * profit_risk +
        0.25 * liquidity_risk +
        0.15 * volatility_risk +
        0.1 * trend_risk +
        0.15 * invest_risk +
        0.05 * fail_rate
    )

    return min(score * 100, 100)


def log_action(item, sell, buy, qty, ppu, profit_day, risk):
    exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["time", "item", "sell", "buy", "qty", "ppu", "profit_day", "risk"])
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            item, sell, buy, int(qty),
            round(ppu, 2), int(profit_day), int(risk)
        ])


def send_discord(item, ppu, profit_day, qty, risk):
    msg = (
        f"**Bazaar Flip Alert**\n"
        f"Item: {item}\n"
        f"Profit/unit: {round(ppu,2)}\n"
        f"Profit/day: {int(profit_day)}\n"
        f"Quantity: {int(qty)}\n"
        f"Risk: {int(risk)}"
    )
    requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})


def load_learning():
    data = {}
    if not os.path.isfile(LEARNING_FILE):
        return data
    with open(LEARNING_FILE, newline="") as f:
        for row in csv.DictReader(f):
            data[row["item"]] = {
                "trades": int(row["trades"]),
                "wins": int(row["wins"]),
                "losses": int(row["losses"]),
                "avg_profit": float(row["avg_profit"]),
                "avg_risk": float(row["avg_risk"])
            }
    return data


def save_learning(mem):
    with open(LEARNING_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["item", "trades", "wins", "losses", "avg_profit", "avg_risk"])
        for k, v in mem.items():
            writer.writerow([
                k, v["trades"], v["wins"], v["losses"],
                round(v["avg_profit"], 2), round(v["avg_risk"], 2)
            ])


def confidence(rec):
    if rec["trades"] < 5:
        return 0.3
    win_rate = rec["wins"] / rec["trades"]
    return max(0.05, win_rate * (1 - rec["avg_risk"] / 100))


class BazaarAgent:
    def __init__(self, purse):
        self.purse = purse
        self.market = {}
        self.learning = load_learning()

    def perceive(self, bazaar):
        obs = {}
        for name, item in bazaar.items():
            sell, buy = get_prices(item)
            s_vol, b_vol = get_daily_volumes(item)
            qty = min(s_vol, b_vol) * 0.8
            if sell > 0 and buy > 0 and qty > 0:
                obs[name] = (sell, buy, qty)
        return obs

    def update(self, obs):
        for name, (sell, buy, qty) in obs.items():
            ppu = profit_per_unit(sell, buy)
            profit_day = ppu * qty
            if profit_day <= 0:
                continue

            pp = ppu / sell
            rec = self.market.setdefault(name, {"prices": []})
            rec["prices"].append(buy)
            rec["prices"] = rec["prices"][-7:]

            prices = rec["prices"]
            avg = sum(prices) / len(prices)
            vol = (max(prices) - min(prices)) / avg if avg else 0
            trend = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0
            invest = min((qty * buy) / self.purse, 1)

            rec.update({
                "sell": sell,
                "buy": buy,
                "qty": qty,
                "ppu": ppu,
                "profit_day": profit_day,
                "pp": pp,
                "vol": vol,
                "trend": trend,
                "invest": invest
            })

    def decide(self):
        picks = []
        for name, d in self.market.items():
            risk = risk_score(
                d["pp"], d["qty"], d["vol"], d["trend"], d["invest"],
                self.learning.get(name, {}).get("losses", 0)
            )

            if name in self.learning:
                risk *= (1 - confidence(self.learning[name]))

            score = d["profit_day"] / max(risk, 1)
            picks.append((score, risk, name, d))

        return sorted(picks, reverse=True)[:5]

    def learn(self, name, profit_day, risk):
        rec = self.learning.setdefault(name, {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "avg_profit": 0.0,
            "avg_risk": 0.0
        })

        rec["trades"] += 1
        rec["wins" if profit_day > 0 else "losses"] += 1

        a = 0.2
        rec["avg_profit"] = (1 - a) * rec["avg_profit"] + a * profit_day
        rec["avg_risk"] = (1 - a) * rec["avg_risk"] + a * risk

        save_learning(self.learning)

    def act(self, picks):
        for score, risk, name, d in picks:
            log_action(name, d["sell"], d["buy"], d["qty"], d["ppu"], d["profit_day"], risk)
            self.learn(name, d["profit_day"], risk)

            if d["ppu"] > 100_000 and d["qty"] > 5_000:
                send_discord(name, d["ppu"], d["profit_day"], d["qty"], risk)

            conf = confidence(self.learning.get(name, {"trades":0,"wins":0,"avg_risk":0}))
            print("\nAI RECOMMENDATION")
            print(f"Item: {name}")
            print(f"Profit/unit: {round(d['ppu'],2)}")
            print(f"Profit/day: {int(d['profit_day'])}")
            print(f"Quantity: {int(d['qty'])}")
            print(f"Risk: {int(risk)}")
            print(f"Confidence: {round(conf,2)}")


agent = BazaarAgent(50_000_000)

while True:
    try:
        data = get_bazaar_data()
        obs = agent.perceive(data)
        agent.update(obs)
        picks = agent.decide()
        agent.act(picks)
        time.sleep(30)
        os.system("cls" if os.name == "nt" else "clear")
    except KeyboardInterrupt:
        break
