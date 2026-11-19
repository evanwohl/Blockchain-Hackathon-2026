from functools import lru_cache

from freightos.funding import demo_plans, priced_plan
from hedging.invoice_policy import invoice_policy, reserve_limit
from hedging.quick_pay import integer, price_calendar


def surcharge_receipt(cents, day):
    return {"id": "fuel surcharge", "face_cents": cents,
            "options": [{"name": "standard", "day": day, "fee_bps": 0}]}


@lru_cache(maxsize=16, typed=True)
def surcharge_plans(cents, day, capacity):
    integer(cents, "fuel surcharge")
    integer(day, "receipt day", 365)
    integer(capacity, "reserve capacity")
    if not cents or not day:
        raise ValueError("Enter a positive surcharge and trip day")
    profile = f"fuel surcharge {cents} day {day} reserve {capacity}"
    quotes = {}
    for scenario, base in demo_plans()[0]["quick pay"].items():
        case = reserve_limit(base["calendar"], capacity)
        case = {**case, "invoices": [*case["invoices"], surcharge_receipt(cents, day)]}
        quotes[scenario] = priced_plan(case, profile, scenario, base["gemini"], base["input_sha256"])
    policy = invoice_policy(quotes)
    if policy is None:
        raise ValueError("No invoice terms cover the fuel budgets for this receipt date")
    plans = {}
    for scenario, quote in quotes.items():
        plan = priced_plan(quote["calendar"], profile, scenario, quote["gemini"], quote["input_sha256"], policy["elections"])
        on_time = day <= plan["calendar"]["bills"][0]["day"]
        plan["surcharge"] = {
            "version": 1, "cents": cents, "day": day, "capacity_cents": capacity,
            "for_fuel_cents": min(cents, plan["other_funds_cents"]) if on_time else 0,
        }
        plans[scenario] = plan
    return plans, policy


@lru_cache(maxsize=16)
def standard_terms_threshold(capacity):
    cases = []
    for plan in demo_plans()[0]["quick pay"].values():
        case = plan["calendar"]
        invoices = [{**i, "options": [o for o in i["options"] if o["name"] == "standard"]} for i in case["invoices"]]
        cases.append({**reserve_limit(case, capacity), "invoices": invoices})

    def covered(cents):
        for case in cases:
            early = surcharge_receipt(cents, case["bills"][0]["day"])
            if price_calendar({**case, "invoices": [*case["invoices"], early]})["status"] != "funded":
                return False
        return True

    low, high = -1, max(case["bills"][0]["cents"] for case in cases)
    while high - low > 1:
        mid = (low + high) // 2
        if covered(mid):
            high = mid
        else:
            low = mid
    return high
