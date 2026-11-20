"""Project the USD cash for a single load before arranging financing."""
from datetime import date

from hedging.quick_pay import integer


def project(data):
    def amount(key):
        return integer(data.get(key), key.replace("_", " "), 100_000_000)

    def when(key):
        value = data.get(key)
        try:
            return date.fromisoformat(value).isoformat()
        except (ValueError, TypeError):
            raise ValueError(f"Enter a date for {key.replace('_', ' ')}") from None

    start = when("opening_date")
    opening = amount("opening_cents")
    floor = amount("floor_cents")
    events = [(start, 0, "Opening USD balance", opening)]
    for key, label in (("freight", "Freight invoice"), ("surcharge", "Customer surcharge")):
        cents = amount(key + "_cents")
        status = data.get(key + "_status")
        if status not in ("expected", "received", "unconfirmed"):
            raise ValueError(f"Choose a status for {label}")
        if cents and status == "expected":
            day = when(key + "_date")
            if key == "surcharge" and data.get("use_offer") is True:
                offered = when("offer_date")
                fee = integer(data.get("offer_fee_cents"), "offer fee", cents)
                if offered >= day:
                    raise ValueError("The offer must pay before the regular surcharge date")
                day, cents, label = offered, cents - fee, "Customer surcharge after fee"
            events.append((day, 1, label + " expected", cents))

    gallons = amount("gallon_hundredths")
    price = amount("pump_mills")
    if not gallons or not price:
        raise ValueError("Enter positive gallons and pump price")
    fuel = (gallons * price + 500) // 1000
    events.append((when("fuel_date"), 2, "Fuel purchase", -fuel))
    rows = []
    balance = 0
    for day, _, label, delta in sorted(events):
        if day < start:
            raise ValueError(f"{label} precedes the opening balance date")
        balance += delta
        rows.append(dict(date=day, label=label, change_cents=delta, balance_cents=balance))
    return dict(rows=rows, fuel_cents=fuel, gap_cents=max(0, floor - min(r["balance_cents"] for r in rows)))
