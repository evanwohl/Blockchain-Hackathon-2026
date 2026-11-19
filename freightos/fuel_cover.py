"""Compare the trip's cash buffer to a hypothetical Gemini oil position."""

import gzip
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from functools import cache

from freightos import fuel_update

SNAPSHOT = fuel_update.SNAPSHOT.with_name("fuel_cover.json.gz")
BOOK = "https://api.gemini.com/v1/book/"
TAKER_RATE = Decimal("0.07")


def up(amount):
    return int(amount.to_integral_value(rounding=ROUND_CEILING))


def cash_gap(oil, fuel):
    change = (oil - Decimal(fuel["reference"])) * Decimal(fuel["cents_per_oil_dollar"])
    return max(0, fuel["base_cents"] + up(change) - fuel["paid_cents"])


def price_position(quantity, asks):
    left = quantity
    premium = fee = 0
    for price, size in asks:
        fill = min(left, size)
        premium += up(100 * fill * price)
        fee += up(100 * TAKER_RATE * fill * price * (1 - price))
        left -= fill
        if left == 0:
            break
    if left:
        raise ValueError("Position exceeds displayed offers")
    return premium, fee


def analyze(snapshot):
    fuel = fuel_update.compare(snapshot["market"])
    _, terms, _, window, event_key = fuel_update.watch_terms()
    symbol = terms["stress_contract"]
    expiry = datetime.fromisoformat(fuel["expiry"])
    strike = Decimal(fuel["threshold"])
    upper = max(Decimal(c["strike"]["value"]) for c in window[event_key]["contracts"]
                if not c["isCombo"] and c["strike"]["type"] == "above")
    gap = cash_gap(upper, fuel)
    below = cash_gap(strike, fuel)
    checked = datetime.fromisoformat(snapshot["checked_at"])
    quote_at = datetime.fromisoformat(fuel["quote_at"])
    result = {"checked_at": snapshot["checked_at"], "expiry": fuel["expiry"], "symbol": symbol,
              "threshold": str(strike), "ceiling": str(upper), "fuel_budget_cents": fuel["paid_cents"],
              "cash_only_cents": gap, "below_strike_cents": below, "position": None}
    book = snapshot["book"]
    if not book or not fuel["yes_ask"] or checked >= expiry:
        return result
    if book["symbol"] != symbol:
        raise ValueError("Order book does not match the trip contract")
    book_at = datetime.fromisoformat(book["reported_at"])
    if not all(-2 <= (checked - t).total_seconds() <= 120 for t in (quote_at, book_at)):
        return result
    target = snapshot["market"]["events"][event_key]["event"]
    contract, = [c for c in target["contracts"] if c["instrumentSymbol"] == symbol]
    step, minimum = (Decimal(contract[k]) for k in ("quantityIncrement", "quantityMinimum"))
    if step != Decimal(".01") or minimum < step or minimum % step:
        raise ValueError("Unsupported Gemini position increments")
    asks = sorted((Decimal(q["price"]), Decimal(q["amount"])) for q in book["levels"]["asks"])
    if any(not 0 < price < 1 or size <= 0 or size % step for price, size in asks):
        raise ValueError("Invalid quoted Gemini depth")
    if not asks or asks[0][0] != Decimal(fuel["yes_ask"]):
        return result
    result.update(ask=str(asks[0][0]), book_at=book["reported_at"])
    capacity = int((sum(size for _, size in asks) / step).to_integral_value(rounding=ROUND_FLOOR))
    best = gap
    min_lots = int(minimum / step)
    for lots in range(min_lots, min(capacity, max(min_lots, gap - below)) + 1):
        quantity = step * lots
        premium, fee = price_position(quantity, asks)
        upfront = premium + fee
        residual = max(below, gap - lots)
        required = upfront + residual
        if required < best:
            result["position"] = {"quantity": str(quantity), "payout_cents": lots, "premium_cents": premium,
                                  "fee_cents": fee, "upfront_cents": upfront, "residual_cents": residual,
                                  "usable_first_cents": required, "fuel_first_cents": upfront + gap}
            best = required
    return result


def capture():
    market = fuel_update.capture()
    fuel = fuel_update.compare(market)
    book = None
    if fuel["yes_ask"]:
        symbol = fuel_update.watch_terms()[1]["stress_contract"]
        stamp, levels = fuel_update.fetch_json(BOOK + symbol)
        book = {"symbol": symbol, "reported_at": stamp, "levels": levels}
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "market": market, "book": book}


@cache
def saved():
    return analyze(json.loads(gzip.decompress(SNAPSHOT.read_bytes())))


def live():
    return analyze(capture())
