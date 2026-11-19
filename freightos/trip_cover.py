"""Size same-expiry Gemini positions against the driver's fuel budget."""

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
from functools import cache
from math import ceil

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from freightos import fuel_cover, fuel_update
from hedging.quick_pay import integer

SNAPSHOT = fuel_cover.SNAPSHOT.with_name("trip_cover.json.gz")
EXAMPLE = {"gallons": 100, "pump_mills": 3999, "budget_cents": 40000}


def validate(trip):
    if not integer(trip.get("gallons"), "gallons", 10000) or not integer(trip.get("pump_mills"), "diesel price", 99999):
        raise ValueError("Enter a positive gallon count and posted diesel price")
    integer(trip.get("budget_cents"), "fuel budget", 100_000_000)


def contracts(market):
    _, _, _, window, key = fuel_update.watch_terms()
    original = fuel_update.instruments(window[key])
    event = market["events"][key]["event"]
    return [c for c in event["contracts"] if not c["isCombo"] and
            (c["instrumentSymbol"], c["id"], c["strike"]["type"], c["strike"]["value"], c["expiryDate"]) in original
            and c["strike"]["type"] == "above"]


def capture():
    market = fuel_update.capture()
    fuel = fuel_update.compare(market)
    active = [c for c in contracts(market) if c["marketState"] == "open" and c["prices"].get("buy", {}).get("yes")]

    def fetch(contract):
        symbol = contract["instrumentSymbol"]
        try:
            stamp, levels = fuel_update.fetch_json(fuel_cover.BOOK + symbol)
            return symbol, {"reported_at": stamp, "levels": levels}
        except (OSError, ValueError):
            return symbol, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        books = dict(pool.map(fetch, active)) if fuel["status"] == "active" else {}
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "market": market, "books": books}


def top_offers(snapshot, fuel, rows):
    checked = datetime.fromisoformat(snapshot["checked_at"])

    def fresh(stamp):
        return -2 <= (checked - datetime.fromisoformat(stamp)).total_seconds() <= 120

    if (fuel["status"] != "active" or not fresh(fuel["quote_at"]) or checked >= datetime.fromisoformat(fuel["expiry"])):
        return []
    offers = []
    for c in rows:
        symbol = c["instrumentSymbol"]
        book = snapshot["books"].get(symbol)
        ask = c["prices"].get("buy", {}).get("yes")
        if (not book or not fresh(book["reported_at"]) or not ask or c["status"] != "active"
                or c["marketState"] != "open" or datetime.fromisoformat(c["effectiveDate"]) > checked):
            continue
        levels = [(Decimal(q["price"]), Decimal(q["amount"])) for q in book["levels"]["asks"]]
        if not levels:
            continue
        price = min(p for p, _ in levels)
        depth = sum(q for p, q in levels if p == price)
        if (any(not 0 < p < 1 or q <= 0 for p, q in levels) or price != Decimal(ask)
                or Decimal(c["quantityMinimum"]) != Decimal(".01") or Decimal(c["quantityIncrement"]) != Decimal(".01")
                or price * 100 % 1 or depth * 100 % 1):
            continue
        offers.append({"symbol": symbol, "strike": c["strike"]["value"], "price": int(price * 100), "depth": int(depth * 100)})
    return sorted(offers, key=lambda o: Decimal(o["strike"]))


def cost(lots, offer):
    p = offer["price"]
    return (lots * p + 99) // 100, (lots * 7 * p * (100 - p) + 999999) // 1000000


def portfolio(offers, gaps):
    gap = gaps[-1]
    empty = {"cash_cents": gap, "upfront_cents": 0, "residual_cents": gap, "legs": []}
    n = len(offers)
    if not n or not gap:
        return empty
    # Variables are hundredths of a contract, premium cents, fee cents, and unhedged cash cents.
    rows, bounds = [], []
    for i, offer in enumerate(offers):
        p = offer["price"]
        for column, coefficient, denominator in ((n + i, p, 100), (2*n + i, 7*p*(100-p), 1000000)):
            row = np.zeros(3*n + 1)
            row[i], row[column] = coefficient, -denominator
            rows.append(row)
            bounds.append(0)
    for i, need in enumerate(gaps):
        row = np.zeros(3*n + 1)
        row[:i], row[-1] = -1, -1
        rows.append(row)
        bounds.append(-need)
    caps = [min(o["depth"], gap) for o in offers] + [gap] * (2*n + 1)
    objective = [0]*n + [1]*(2*n + 1)
    constraints = LinearConstraint(rows, -np.inf, bounds)
    sol = milp(objective, integrality=np.ones(3*n + 1), bounds=Bounds(0, caps), constraints=constraints,
               options={"mip_rel_gap": 0, "time_limit": 3})
    if not sol.success:
        raise ValueError("Could not establish the minimum cash requirement")
    lots = [int(round(q)) for q in sol.x[:n]]
    prefix = 0
    residual = gaps[0]
    legs = []
    for i, (qty, offer) in enumerate(zip(lots, offers)):
        if not 0 <= qty <= caps[i]:
            raise ValueError("Position exceeds the available quantity")
        if qty:
            premium, fee = cost(qty, offer)
            legs.append({**offer, "lots": qty, "premium_cents": premium, "fee_cents": fee})
        prefix += qty
        residual = max(residual, gaps[i+1] - prefix)
    upfront = sum(leg["premium_cents"] + leg["fee_cents"] for leg in legs)
    total = residual + upfront
    if abs(total - sol.fun) > .001:
        raise ValueError("Rounded position does not reconcile with the optimum")
    return {"cash_cents": total, "upfront_cents": upfront, "residual_cents": residual, "legs": legs} if total < gap else empty


def analyze(snapshot, trip):
    validate(trip)
    market = snapshot["market"]
    fuel = fuel_update.compare(market)
    rows = contracts(market)
    ceiling = max(Fraction(c["strike"]["value"]) for c in rows)
    anchor = Fraction(fuel["close"])
    base = Fraction(trip["pump_mills"] * trip["gallons"], 10)
    slope = Fraction(fuel["cents_per_oil_dollar"]) * trip["gallons"] / int(fuel["gallons"])

    def gap(oil):
        return max(0, ceil(base + (oil - anchor)*slope) - trip["budget_cents"])

    offers = top_offers(snapshot, fuel, rows)
    gaps = [gap(Fraction(o["strike"])) for o in offers] + [gap(ceiling)]
    combined = portfolio(offers, gaps)
    single = min((portfolio([o], [g, gaps[-1]])["cash_cents"] for o, g in zip(offers, gaps)), default=gaps[-1])
    return {"trip": trip, "checked_at": snapshot["checked_at"], "expiry": fuel["expiry"], "anchor": str(fuel["close"]),
            "ceiling": str(ceiling), "pump_bill_cents": ceil(base), "cash_only_cents": gaps[-1], "best_single_cents": single,
            "offers": len(offers), "contracts_checked": len(rows), "combined": combined}


@cache
def recorded():
    return json.loads(gzip.decompress(SNAPSHOT.read_bytes()))


@cache
def saved():
    return analyze(recorded(), EXAMPLE)
