import requests
import time
import os
import csv
from datetime import datetime

API_KEY = "f09313b8-05e5-4327-a890-907c5483b71d"
URL = "https://api.hypixel.net/skyblock/bazaar"

DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1456660655313780840/vk7bTXn7z94l4UxQdBJRaBUvKAPJqHpFCsACx6W6bYfnR4uR-UtWQP9Frqbqg0UCjHRK"


def get_bazaar_data():
    try:
        r = requests.get(URL, params={"key": API_KEY}, timeout=10)
        r.raise_for_status()
        return r.json().get("products", {})
    except:
        return {}
    

def send_discord(item, ppu, profit_day, qty, risk):
    msg = (
       f"Bazaar Price Alert\n"
        f"Item: {item}\n"
        f"Sell Price: {round(price,2)}"
    )
    requests.post(DISCORD_WEBHOOK_URL, json={"content": msg})

def sell_value(item):
    sell = item.get("sell_summary", [])
    return sell[0]["pricePerUnit"] if sell else 0

item_id = input("ENTER THE ITEM: ").strip().upper()
PRICE_TRIGGER = 13000
CHECK_DELAY = 180
LAST_ALERT = 0

while True:
    try:
        if sell_value(item) > 13000:
            send_discord("PRICE SURGE, BE QUICK")
    
    except KeyboardInterrupt:
        break
        