import requests # If error with module not found use 'pip install requests' in terminal
import time
import os

print("Bazaar Dashboard starting...")

API_KEY = "f09313b8-05e5-4327-a890-907c5483b71d"
URL = "https://api.hypixel.net/skyblock/bazaar"



def get_bazaar_data():
    response = requests.get(URL, params={"key": API_KEY})
    return response.json()["products"]

def get_prices(item):
    buy = item["buy_summary"][0]["pricePerUnit"] if item["buy_summary"] else 0
    sell = item["sell_summary"][0]["pricePerUnit"] if item["sell_summary"] else 0
    return buy, sell

def get_profit_item(buy, sell):
    return sell - buy

def read_specific_product(bazaar):
    item_name = input("Enter the name of the product you want to read:\n").upper()
    if item_name in bazaar:
        item = bazaar[item_name]
        buy_price, sell_price = get_prices(item)
        profit = get_profit_item(buy_price, sell_price)
        print(f"Name: {item_name}")
        print("Buy price:", buy_price)
        print("Sell price:", sell_price)
        print("Profit per unit:", profit)
    else:
        print(f"{item_name} not found in bazaar")

while True:
    os.system("cls" if os.name == "nt" else "clear")
    bazaar = get_bazaar_data()
    profits = []

    for name, item in bazaar.items():
        buy, sell = get_prices(item)
        if buy > 0 and sell > 0:
            profit = get_profit_item(buy, sell)
            profits.append((name, profit))

    profits.sort(key=lambda x: x[1], reverse=True)

    print("\nTOP 10 PROFITS:")
    for name, profit in profits[:10]:
        print(f"{name:30} {profit:.2f} coins")

    read_specific_product(bazaar)

    time.sleep(30)


