from itertools import product

from hedging.quick_pay import integer, price_calendar


RESERVE = "reserve redemption"


def invoice_choices(plans):
    if not plans:
        raise ValueError("At least one fuel budget is required")
    invoices = next(iter(plans.values()))["calendar"]["invoices"]
    if any(p["calendar"]["invoices"] != invoices for p in plans.values()):
        raise ValueError("Invoice terms must be shared across fuel budgets")
    for options in product(*(invoice["options"] for invoice in invoices)):
        restricted = [{**invoice, "options": [option]} for invoice, option in zip(invoices, options)]
        groups = {}
        for invoice, option in zip(invoices, options):
            group = invoice.get("election_group")
            if group and groups.setdefault(group, option["name"]) != option["name"]:
                break
        else:
            yield restricted


def reserve_limit(case, capacity):
    return {**case, "funding": [
        {**s, "net_capacity_cents": min(s["net_capacity_cents"], capacity)} if s["id"] == RESERVE else s
        for s in case["funding"]
    ]}


def _minimum_capacity(cases, plans, allowance):
    # With fixed invoice terms, each fuel outcome has its own draw. Their maximum
    # is the least common capacity that satisfies every scenario's fee limit.
    minimum = 0
    for key, case in cases.items():
        limit = plans[key]["cost_cents"] + allowance
        outcome = price_calendar(case, minimize_source=RESERVE, fee_limit_cents=limit)
        if outcome["status"] != "funded":
            return None
        minimum = max(minimum, _reserve_draw(outcome))
    return minimum


def invoice_policy(plans, *, fee_allowance_cents=None, comparison_costs=None):
    """Choose common invoice terms before fuel is known and size draws after each outcome."""
    if fee_allowance_cents is not None:
        integer(fee_allowance_cents, "fee allowance")
    best = None
    for invoices in invoice_choices(plans):
        cases = {k: {**p["calendar"], "invoices": invoices} for k, p in plans.items()}
        if fee_allowance_cents is not None:
            minimum = _minimum_capacity(cases, plans, fee_allowance_cents)
            if minimum is None:
                continue
            cases = {k: reserve_limit(case, minimum) for k, case in cases.items()}
        results = {}
        for key, case in cases.items():
            outcome = price_calendar(case)
            if outcome["status"] != "funded":
                break
            results[key] = {"cost_cents": outcome["cost_cents"], "reserve_cents": _reserve_draw(outcome),
                            "regret_cents": outcome["cost_cents"] - plans[key]["cost_cents"]}
        if len(results) != len(cases):
            continue
        regret = max(row["regret_cents"] for row in results.values())
        capacity = max(row["reserve_cents"] for row in results.values())
        elections = {i["id"]: i["options"][0]["name"] for i in invoices}
        rank = (regret, max(row["cost_cents"] for row in results.values()), capacity,
                sum(option != "standard" for option in elections.values()))
        if comparison_costs is not None:
            worst_difference = max(row["cost_cents"] - comparison_costs[key] for key, row in results.items())
            rank = (worst_difference, *rank)
        if fee_allowance_cents is not None:
            rank = (capacity, *rank)
        if best is None or rank < best[0]:
            best = rank, {"elections": elections, "worst_regret_cents": regret,
                          "peak_reserve_cents": capacity, "outcomes": results}
    return best[1] if best else None


def _reserve_draw(outcome):
    return sum(d["net_proceeds_cents"] for d in outcome["funding"] if d["source"] == RESERVE)
