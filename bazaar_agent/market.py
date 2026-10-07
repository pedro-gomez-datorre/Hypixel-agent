"""Bazaar API access and parsing."""
import logging
import time
from dataclasses import dataclass

import requests

URL = "https://api.hypixel.net/skyblock/bazaar"
log = logging.getLogger(__name__)


class BazaarError(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    """One product at one instant.

    The API names are inverted relative to intuition, so we rename them:
      bid = highest buy order    (API ``sell_summary[0]``) -> what you pay placing a buy order
      ask = lowest sell offer    (API ``buy_summary[0]``)  -> what you receive placing a sell offer
    A flip is: buy order at ``bid``, later sell offer at ``ask`` (minus tax).
    """
    name: str
    bid: float
    ask: float
    buy_flow: float   # items/day that fill your buy order (others instant-selling)
    sell_flow: float  # items/day that fill your sell offer (others instant-buying)


def parse_quote(name: str, item: dict) -> Quote | None:
    bids = item.get("sell_summary") or []
    asks = item.get("buy_summary") or []
    if not bids or not asks:
        return None
    qs = item.get("quick_status") or {}
    return Quote(
        name=name,
        bid=float(bids[0]["pricePerUnit"]),
        ask=float(asks[0]["pricePerUnit"]),
        buy_flow=qs.get("sellMovingWeek", 0) / 7,
        sell_flow=qs.get("buyMovingWeek", 0) / 7,
    )


def fetch_quotes(api_key: str = "", retries: int = 3, session: requests.Session | None = None) -> dict[str, Quote]:
    """Fetch every product. Retries with backoff; raises BazaarError if all attempts fail."""
    http = session or requests
    headers = {"API-Key": api_key} if api_key else {}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = http.get(URL, headers=headers, timeout=10)
            r.raise_for_status()
            payload = r.json()
            if not payload.get("success", True):
                raise BazaarError(payload.get("cause", "API reported failure"))
            products = payload["products"]
            quotes = (parse_quote(n, i) for n, i in products.items())
            return {q.name: q for q in quotes if q}
        except (requests.RequestException, ValueError, KeyError, BazaarError) as e:
            last = e
            log.warning("bazaar fetch failed (%d/%d): %s", attempt + 1, retries, e)
            time.sleep(2 ** attempt)
    raise BazaarError(f"could not fetch bazaar data: {last}")
