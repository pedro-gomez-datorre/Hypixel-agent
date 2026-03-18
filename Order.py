import requests
import time
import os

print("Bazaar Dashboard (IB ↔ SO with daily volume and tax) starting...")

API_KEY = "f09313b8-05e5-4327-a890-907c5483b71d"
URL = "https://api.hypixel.net/skyblock/bazaar"

TAX = 0.0125

def get_bazaar_data():
    response = requests.get(URL, params={"key": API_KEY})
    return response.json()["products"]

def get_prices(item):
    sell_order = item["sell_summary"][0]["pricePerUnit"] if item["sell_summary"] else 0
    buy_order = item["buy_summary"][0]["pricePerUnit"] if item["buy_summary"] else 0
    return sell_order, buy_order

def get_daily_volumes(item):
    if "quick_status" in item:
        daily_sell = item["quick_status"]["sellMovingWeek"] / 7
        daily_buy = item["quick_status"]["buyMovingWeek"] / 7
        return daily_sell, daily_buy
    return 0, 0

def get_profit(item):
    sell_order, buy_order = get_prices(item)
    profit_per_unit = (buy_order * (1 - TAX)) - sell_order
    daily_sell, daily_buy = get_daily_volumes(item)
    safe_quantity = min(daily_sell, daily_buy) * 0.8
    total_profit = profit_per_unit * safe_quantity
    return profit_per_unit, total_profit, safe_quantity

while True:
    os.system("cls" if os.name == "nt" else "clear")
    bazaar = get_bazaar_data()
    profits = []

    for name, item in bazaar.items():
        profit_per_unit, total_profit, safe_quantity = get_profit(item)
        if profit_per_unit > 0 and safe_quantity > 0:
            profits.append((name, profit_per_unit, total_profit, safe_quantity))

    profits.sort(key=lambda x: x[2], reverse=True)

    print("\nTOP 10 PROFITS (IB ↔ SO with daily volume and tax):")
    for name, ppu, tp, sq in profits[:10]:
        print(f"{name:30} Profit/unit: {ppu:.2f} | Total Profit: {tp:.2f} | Safe Qty: {sq:.0f}")

    time.sleep(30)
