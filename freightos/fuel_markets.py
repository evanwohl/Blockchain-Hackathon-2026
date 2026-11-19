import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from functools import cache

from freightos.capture import ROOT


@cache
def recorded_levels(checksum, as_of, contract, index, lower, upper, gallons, pass_through):
    raw = (ROOT / "artifacts/gemini_ladder/active.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("Gemini contract snapshot differs from the funding evidence")
    events = [event for event in json.loads(raw)["data"] if any(
        item["instrumentSymbol"] == contract for item in event["contracts"]
    )]
    if len(events) != 1 or events[0]["sourceDetails"]["index"] != index:
        raise ValueError("Gemini funding contracts must match one settlement index and expiry")
    event = events[0]
    observed = datetime.fromisoformat(as_of)
    expiry = datetime.fromisoformat(event["expiryDate"])
    if observed >= expiry or Decimal(lower) >= Decimal(upper):
        raise ValueError("Invalid recorded funding window")
    cents_per_dollar = Decimal(gallons) * Decimal(pass_through) * 100 / 42
    levels = {}
    for item in event["contracts"]:
        if (item["strike"]["type"] != "above" or item["isCombo"] or item["status"] != "active"
                or item["marketState"] != "open" or datetime.fromisoformat(item["effectiveDate"]) > observed
                or datetime.fromisoformat(item["expiryDate"]) != expiry):
            continue
        strike = Decimal(item["strike"]["value"])
        if strike <= Decimal(upper):
            continue
        level = str(strike)
        if level in levels:
            raise ValueError("Duplicate Gemini fuel threshold")
        interval = [int(((strike - Decimal(base)) * cents_per_dollar).to_integral_value(rounding=ROUND_CEILING))
                    for base in (upper, lower)]
        levels[level] = {
            "stress_level": level, "stress_contract": item["instrumentSymbol"],
            "extra_fuel_cents_interval": interval, "expiry": event["expiryDate"],
            "recorded_buy_yes": item["prices"].get("buy", {}).get("yes"),
        }
    return dict(sorted(levels.items(), key=lambda pair: Decimal(pair[0])))


def fuel_levels(evidence):
    g = evidence["gemini"]
    return recorded_levels(evidence["input_sha256"]["artifacts/gemini_ladder/active.json"], evidence["as_of"],
                           g["stress_contract"], g["index"], g["lower_inclusive"], g["upper_exclusive"],
                           g["gallons"], g["pass_through"])
