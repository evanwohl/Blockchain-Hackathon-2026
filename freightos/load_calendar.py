"""Project the USD cash for a single load before arranging financing."""
from datetime import date

from hedging.quick_pay import integer, price_calendar


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


def funding_options(data, projection, reserve=None):
    """Price the selected receipt terms against only the credit the carrier confirms."""
    start = date.fromisoformat(data["opening_date"])

    def day(value):
        try:
            offset = (date.fromisoformat(value) - start).days
        except (ValueError, TypeError):
            raise ValueError("Enter valid funding availability and repayment or refill dates") from None
        return integer(offset, "date relative to opening balance")

    sources = []
    if data.get("use_bank") is True:
        available = day(data.get("bank_date"))
        maturity = day(data.get("bank_due"))
        if maturity <= available:
            raise ValueError("Bank repayment must follow availability")
        sources.append(dict(id="Approved bank line", available_day=available, restore_day=maturity,
                            net_capacity_cents=integer(data.get("bank_cents"), "approved credit limit", 100_000_000),
                            term_cost_bps=integer(data.get("bank_bps"), "fee for the full term", 10000),
                            fixed_cost_cents=integer(data.get("bank_fixed_cents"), "fixed draw fee", 100_000_000)))

    bank_only = sources.copy()
    if reserve and reserve["capacity_cents"]:
        arrival = day(data.get("reserve_date"))
        refill = day(data.get("refill_date"))
        if arrival <= day(data["fuel_date"]):
            if refill <= arrival:
                raise ValueError("Refill the reserve after the redemption date")
            sources.append(dict(id="Kamino reserve", available_day=arrival, restore_day=refill,
                                net_capacity_cents=min(reserve["capacity_cents"], projection["fuel_cents"]),
                                term_cost_bps=reserve["term_cost_bps"], fixed_cost_cents=reserve["fixed_cost_cents"]))

    def solve(view, offer_fee, menu):
        receipts = [dict(id=r["label"], face_cents=r["change_cents"], options=[
            dict(name="agreed", day=day(r["date"]), fee_bps=0)])
            for r in view["rows"] if r["label"] not in ("Fuel purchase", "Opening USD balance")]
        opening = data["opening_cents"]
        floor = data["floor_cents"]
        case = dict(reserve_floor_cents=floor, bills=[dict(id="Fuel purchase", day=day(data["fuel_date"]),
                    cents=view["fuel_cents"])], invoices=receipts, funding=menu)

        def price(extra):
            if opening + extra < floor:
                return {"status": "unfunded"}
            return price_calendar(dict(case, opening_cash_cents=opening + extra))

        plan = price(0)
        if plan["status"] == "funded":
            plan["cost_cents"] += offer_fee
            return plan
        # The no-credit shortfall is a feasible upper bound. Earlier repayment
        # can make the required top-up larger than the shortfall on fuel day alone.
        high = view["gap_cents"]
        low = 0
        if menu:
            while high - low > 1:
                middle = (low + high) // 2
                if price(middle)["status"] == "funded":
                    high = middle
                else:
                    low = middle
        return dict(status="unfunded", gap_cents=high)

    early = (data.get("use_offer") is True and data["surcharge_status"] == "expected"
             and data["surcharge_cents"] > 0)
    regular = project(dict(data, use_offer=False)) if early else projection
    fee = data["offer_fee_cents"] if early else 0
    selected = solve(projection, fee, sources)
    waiting = solve(regular, 0, sources) if early else None
    result = dict(selected=selected, waiting=waiting)
    if reserve is not None:
        alternatives = [("Wait for regular receipts", solve(regular, 0, bank_only))]
        if early:
            alternatives.append(("Take the early offer", solve(projection, fee, bank_only)))
        label, backup = min(alternatives, key=lambda item: (
            item[1]["status"] != "funded", item[1].get("gap_cents", 0), item[1].get("cost_cents", 0)))
        result.update(reserve=reserve, backup=backup, backup_terms=label)
    return result
