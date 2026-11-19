from itertools import product

from hedging.quick_pay import price_calendar


def invoice_policy(plans):
    """Choose invoice terms before fuel is known; minimize the worst fee premium over hindsight."""
    if not plans:
        raise ValueError("At least one fuel budget is required")
    first = next(iter(plans.values()))
    invoices = first["calendar"]["invoices"]
    if any(p["calendar"]["invoices"] != invoices for p in plans.values()):
        raise ValueError("Invoice terms must be shared across fuel budgets")
    best = None
    for elections in product(*(invoice["options"] for invoice in invoices)):
        restricted = [{**invoice, "options": [option]} for invoice, option in zip(invoices, elections)]
        results = {}
        for scenario, plan in plans.items():
            outcome = price_calendar({**plan["calendar"], "invoices": restricted})
            if outcome["status"] != "funded":
                break
            reserve = sum(draw["net_proceeds_cents"] for draw in outcome["funding"]
                          if draw["source"] == "reserve redemption")
            results[scenario] = {"cost_cents": outcome["cost_cents"], "reserve_cents": reserve,
                                 "regret_cents": outcome["cost_cents"] - plan["cost_cents"]}
        if len(results) != len(plans):
            continue
        regret = max(row["regret_cents"] for row in results.values())
        capacity = max(row["reserve_cents"] for row in results.values())
        rank = (regret, max(row["cost_cents"] for row in results.values()), capacity,
                sum(option["name"] != "standard" for option in elections))
        if best is None or rank < best[0]:
            best = rank, {
                "elections": {i["id"]: o["name"] for i, o in zip(invoices, elections)},
                "worst_regret_cents": regret, "peak_reserve_cents": capacity, "outcomes": results,
            }
    return best[1] if best else None
