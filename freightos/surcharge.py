from functools import lru_cache

from freightos.funding import demo_plans, priced_plan
from hedging.invoice_policy import invoice_choices, invoice_policy, reserve_limit
from hedging.quick_pay import integer, price_calendar


def surcharge_receipt(cents, day, fee=0):
    terms = {"name": "standard", "day": day, "fee_bps": 0}
    if fee:
        terms["fixed_fee_cents"] = fee
    return {"id": "fuel surcharge", "face_cents": cents, "options": [terms]}


@lru_cache(maxsize=16, typed=True)
def surcharge_plans(cents, day, capacity, fee=0, regular_day=None):
    integer(cents, "fuel surcharge")
    integer(day, "receipt day", 365)
    integer(capacity, "reserve capacity")
    if not cents or not day:
        raise ValueError("Enter a positive surcharge and trip day")
    integer(fee, "offer fee", cents)
    comparison = None
    if regular_day is not None:
        integer(regular_day, "regular payment day", 365)
        if regular_day <= day:
            raise ValueError("The offered payment must clear before the regular payment")
        waiting, _ = surcharge_plans(cents, regular_day, capacity)
        comparison = {key: plan["cost_cents"] for key, plan in waiting.items()}
    elif fee:
        raise ValueError("Enter the regular payment day for this offer")
    profile = f"fuel surcharge {cents} day {day} reserve {capacity}"
    if comparison is not None:
        profile += f" fee {fee} regular {regular_day}"
    quotes = {}
    for scenario, base in demo_plans()[0]["quick pay"].items():
        case = reserve_limit(base["calendar"], capacity)
        case = {**case, "invoices": [*case["invoices"], surcharge_receipt(cents, day, fee)]}
        quotes[scenario] = priced_plan(case, profile, scenario, base["gemini"], base["input_sha256"])
    policy = invoice_policy(quotes, comparison_costs=comparison)
    if policy is None:
        raise ValueError("No invoice terms cover the fuel budgets for this receipt date")
    plans = {}
    for scenario, quote in quotes.items():
        plan = priced_plan(quote["calendar"], profile, scenario, quote["gemini"], quote["input_sha256"], policy["elections"])
        on_time = day <= plan["calendar"]["bills"][0]["day"]
        plan["surcharge"] = {
            "version": 1, "cents": cents, "day": day, "capacity_cents": capacity,
            "for_fuel_cents": min(cents - fee, plan["other_funds_cents"]) if on_time else 0,
        }
        if comparison is not None:
            plan["surcharge"].update(version=2, fee_cents=fee, regular_day=regular_day)
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


@lru_cache(maxsize=16)
def highest_early_fee(cents, day, regular_day, capacity):
    early, _ = surcharge_plans(cents, day, capacity)
    waiting, _ = surcharge_plans(cents, regular_day, capacity)
    best = -1
    for invoices in invoice_choices(early):
        ceiling = cents
        for key, plan in early.items():
            limit = waiting[key]["cost_cents"] - 1
            if limit <= best:
                ceiling = -1
                break
            case = {**plan["calendar"], "invoices": invoices}
            result = price_calendar(case, maximize_discount_on="fuel surcharge", fee_limit_cents=limit)
            ceiling = min(ceiling, result.get("max_discount_cents", -1))
            if ceiling <= best:
                break
        best = max(best, ceiling)
    return best if best >= 0 else None


def offer_decision(cents, day, regular_day, fee, capacity, scenario):
    early, early_policy = surcharge_plans(cents, day, capacity, fee, regular_day)
    waiting, wait_policy = surcharge_plans(cents, regular_day, capacity)
    savings = min(plan["cost_cents"] - early[key]["cost_cents"] for key, plan in waiting.items())
    take_offer = savings > 0
    offer = {"early_day": day, "regular_day": regular_day, "fee_cents": fee,
             "highest_fee_cents": highest_early_fee(cents, day, regular_day, capacity),
             "early_cost_cents": early[scenario]["cost_cents"], "wait_cost_cents": waiting[scenario]["cost_cents"],
             "least_saved_cents": savings, "take_offer": take_offer}
    return (early, early_policy, offer) if take_offer else (waiting, wait_policy, offer)
