from functools import lru_cache

from freightos import public_offer
from freightos.funding import demo_plans
from freightos.surcharge import surcharge_receipt
from hedging.invoice_policy import RESERVE, invoice_policy, reserve_limit
from hedging.quick_pay import integer, price_calendar


def _price_terms(templates, receipt, cash, reserve, scenario, waiting=None):
    quotes = {}
    for name, base in templates.items():
        calendar = reserve_limit(base["calendar"], reserve)
        source = next(s for s in calendar["funding"] if s["id"] == RESERVE)
        calendar.update(opening_cash_cents=calendar["opening_cash_cents"] + cash,
                        invoices=[*calendar["invoices"], receipt],
                        bills=[*calendar["bills"], {"id": "restore trip cash", "cents": cash, "day": source["restore_day"]}])
        quotes[name] = {"calendar": calendar, "cost_cents": price_calendar(calendar)["cost_cents"]}
    policy = invoice_policy(quotes, comparison_costs=waiting)
    case = quotes[scenario]["calendar"]
    invoices = [{**i, "options": [o for o in i["options"] if o["name"] == policy["elections"][i["id"]]]}
                for i in case["invoices"]]
    plan = price_calendar({**case, "invoices": invoices})
    bill = case["bills"][0]
    early = {p["invoice"]: p["receipt_cents"] for p in plan["payments"] if p["day"] <= bill["day"]}
    draw = sum(s["net_proceeds_cents"] for s in plan["funding"] if s["source"] == RESERVE)
    due = bill["cents"] - draw
    customer = min(due, early.pop(receipt["id"], 0))
    freight = min(due - customer, sum(early.values()))
    spent = due - customer - freight
    if not 0 <= spent <= cash:
        raise ValueError("Fuel payment sources do not reconcile")
    peak = max(policy["outcomes"], key=lambda key: (policy["outcomes"][key]["reserve_cents"],
                                                   quotes[key]["calendar"]["bills"][0]["cents"]))
    return {"peak_draw_cents": policy["outcomes"][peak]["reserve_cents"],
            "peak_bill_cents": quotes[peak]["calendar"]["bills"][0]["cents"], "cost_cents": plan["cost_cents"], "reserve_cents": draw, "cash_cents": spent,
            "customer_cents": customer, "freight_cents": freight, "early_freight_cents": sum(early.values()),
            "scenario_costs": {name: row["cost_cents"] for name, row in policy["outcomes"].items()}}


@lru_cache(maxsize=64, typed=True)
def compare(cash_cents, reserve_available):
    plan, policy, _ = public_offer.price_offer()
    capacity = policy["peak_reserve_cents"]
    integer(cash_cents, "cash portion of the fuel money", capacity)
    if type(reserve_available) is not bool:
        raise ValueError("Choose whether the reserve can pay by the fuel date")
    reserve = capacity - cash_cents if reserve_available else 0
    terms = plan["surcharge"]
    templates = demo_plans()[0]["quick pay"]
    args = (cash_cents, reserve, plan["scenario"])
    wait = _price_terms(templates, surcharge_receipt(terms["cents"], terms["regular_day"]), *args)
    early = _price_terms(templates, surcharge_receipt(terms["cents"], terms["day"], terms["fee_cents"]), *args,
                         waiting=wait["scenario_costs"])
    differences = [cost - early["scenario_costs"][name] for name, cost in wait["scenario_costs"].items()]
    return {"capacity_cents": capacity, "cash_limit_cents": cash_cents, "reserve_limit_cents": reserve,
            "bill_cents": plan["bill_cents"], "budgets": len(differences), "early": early, "wait": wait,
            "choice": "early" if min(differences) > 0 else "wait", "least_early_saving_cents": min(differences)}
