"""Follow the Gemini events that were in the paid offer's original fuel window."""

import gzip
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from email.utils import parsedate_to_datetime
from functools import cache
from urllib.request import Request, urlopen

from freightos import public_offer
from freightos.capture import ROOT
from freightos.fuel_markets import reported_close, settlement_budget
from freightos.funding import DATA

API = "https://api.gemini.com/v1/prediction-markets/events/"
SNAPSHOT = DATA / "fuel_followup.json.gz"


@cache
def watch_terms():
    proof = public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes()))
    decision = proof["decision"]
    evidence = json.loads((DATA / "case_report.json").read_bytes())
    g = settlement_budget(evidence)
    events = json.loads((ROOT / "artifacts/gemini_ladder/active.json").read_bytes())["data"]
    window = {e["ticker"]: e for e in events if e["series"] == "WTI" and e["sourceDetails"]["index"] == g["index"]
              and datetime.fromisoformat(evidence["as_of"]) < datetime.fromisoformat(e["expiryDate"]) <= datetime.fromisoformat(g["expiry"])}
    target, = [e for e in window.values() if any(c["instrumentSymbol"] == g["stress_contract"] for c in e["contracts"])]
    original = decision["bill_cents"] - evidence["gemini"]["extra_fuel_cents_interval"][1]
    return decision, g, original, window, target["ticker"]


def fetch_json(url):
    request = Request(url, headers={"User-Agent": "FreightOS/0.1", "Accept": "application/json"})
    with urlopen(request, timeout=5) as response:
        date = response.headers.get("Date")
        if not date:
            raise ValueError("Missing Gemini response date")
        stamp = parsedate_to_datetime(date).isoformat()
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Gemini event exceeds the expected size")
    return stamp, json.loads(raw)


def fetch_event(ticker):
    stamp, event = fetch_json(API + ticker)
    return {"reported_at": stamp, "event": event}


def capture(fetch=fetch_event):
    _, _, _, window, _ = watch_terms()
    events = {ticker: fetch(ticker) for ticker in window}
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "events": events}


def instruments(record):
    rows = [c for c in record["contracts"] if not c["isCombo"]]
    if len({c["instrumentSymbol"] for c in rows}) != len(rows):
        raise ValueError("Duplicate Gemini contract symbol")
    return {(c["instrumentSymbol"], c["id"], c["strike"]["type"], c["strike"]["value"], c["expiryDate"])
            for c in rows}


def compare(snapshot):
    decision, g, base, window, ticker = watch_terms()
    checked = datetime.fromisoformat(snapshot["checked_at"])
    if set(snapshot["events"]) != set(window):
        raise ValueError("Incomplete Gemini fuel window")
    price = Decimal(g["settled_reference"])
    latest = None
    latest_expiry = None
    for key, original in window.items():
        sample = snapshot["events"][key]
        event = sample["event"]
        stamp = datetime.fromisoformat(sample["reported_at"])
        expiry = datetime.fromisoformat(event["expiryDate"])
        if (stamp > checked + timedelta(seconds=2) or expiry != datetime.fromisoformat(original["expiryDate"])
                or any(event[k] != original[k] for k in ("id", "ticker", "series", "sourceDetails"))):
            raise ValueError("Gemini event identity or timestamp differs from the recorded window")
        if not instruments(original).issubset(instruments(event)):
            raise ValueError("Gemini contracts differ from the recorded fuel window")
        if event["status"] == "settled":
            close, _ = reported_close(event, stamp)
            if latest_expiry is None or expiry > latest_expiry:
                price, latest, latest_expiry = close, event, expiry

    target = snapshot["events"][ticker]
    event = target["event"]
    contract, = [c for c in event["contracts"] if c["instrumentSymbol"] == g["stress_contract"]]
    if (contract["isCombo"] or contract["strike"]["type"] != "above" or Decimal(contract["strike"]["value"]) != Decimal(g["stress_level"])
            or datetime.fromisoformat(contract["expiryDate"]) != datetime.fromisoformat(g["expiry"])):
        raise ValueError("Gemini threshold differs from the original fuel contract")
    stamp = datetime.fromisoformat(target["reported_at"])
    trading = (event["status"] == contract["status"] == "active" and contract["marketState"] == "open"
               and datetime.fromisoformat(contract["effectiveDate"]) <= min(stamp, checked)
               and checked < datetime.fromisoformat(g["expiry"]))
    fresh = (checked - stamp).total_seconds() <= 120
    ask = contract["prices"].get("buy", {}).get("yes") if trading and fresh else None
    if ask is not None and not Decimal(0) <= Decimal(ask) <= Decimal(1):
        raise ValueError("Invalid Gemini YES offer")
    reference = Decimal(g["settled_reference"])
    multiplier = Decimal(g["gallons"]) * Decimal(g["pass_through"]) * 100 / 42
    marked = max(0, base + int(((price - reference) * multiplier).to_integral_value(rounding=ROUND_CEILING)))
    paid = decision["bill_cents"]
    last_covered = (reference + (paid - base) / multiplier).quantize(Decimal(".01"), rounding=ROUND_FLOOR)
    return {"checked_at": snapshot["checked_at"], "base_cents": base, "paid_cents": paid, "marked_cents": marked,
            "headroom_cents": paid - marked, "reference": str(reference), "close": str(price),
            "close_ticker": latest["ticker"] if latest else g["prior_event"],
            "close_at": latest["resolvedAt"] if latest else None, "gallons": g["gallons"], "cents_per_oil_dollar": str(multiplier),
            "customer_cents": decision["outside_cents"], "reserve_needed_cents": max(0, marked - decision["outside_cents"]),
            "paid_reserve_cents": decision["reserve_cents"], "last_covered_oil": str(last_covered),
            "first_uncovered_oil": str(last_covered + Decimal(".01")), "target_ticker": ticker,
            "threshold": g["stress_level"], "expiry": g["expiry"], "status": event["status"], "open": trading,
            "target_outcome": contract["resolutionSide"] if event["status"] == "settled" else None,
            "quote_fresh": fresh, "yes_ask": ask, "quote_at": target["reported_at"], "recorded_ask": g["recorded_buy_yes"]}


def live():
    return compare(capture())


@cache
def saved():
    return compare(json.loads(gzip.decompress(SNAPSHOT.read_bytes())))
