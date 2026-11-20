from itertools import accumulate

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


def integer(value, name, limit=10**12):
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError(f"Invalid {name}")
    return value


def delay_bound(row, expected_key, latest_key):
    expected = integer(row[expected_key], expected_key)
    latest = integer(row.get(latest_key, expected), latest_key)
    if latest < expected:
        raise ValueError("Latest availability precedes expected availability")
    return latest


def price_calendar(case, *, minimize_source=None, fee_limit_cents=None, maximize_discount_on=None):
    """Prices a single USD payment rail. Each step settles receipts before its bills."""
    opening = integer(case["opening_cash_cents"], "opening cash")
    floor = integer(case["reserve_floor_cents"], "cash reserve")
    if opening < floor:
        raise ValueError("Opening cash is below the protected reserve")
    invoices, sources = case["invoices"], case["funding"]
    bills = case["bills"]
    names = [x["id"] for x in [*invoices, *sources, *bills]]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(
        names
    ):
        raise ValueError("Each invoice, bill and funding source needs a unique ID")

    columns, costs, caps, volume, constraints = [], [], [], [], []
    obligations = {}
    for b in bills:
        day = integer(b["day"], "bill day")
        earliest = integer(b.get("earliest_day", day), "earliest bill day")
        amount = integer(b["cents"], "bill amount")
        maximum = integer(b.get("max_cents", amount), "maximum bill amount")
        if earliest > day or maximum < amount:
            raise ValueError("Bill forecast lies outside its stress bounds")
        obligations[earliest] = obligations.get(earliest, 0) + maximum

    def column(cap, cost=0, principal=0, flows=None):
        columns.append(flows or {})
        costs.append(cost)
        caps.append(cap)
        volume.append(principal)
        return len(columns) - 1

    variable_discount = None
    choices = []
    grouped_elections = {}
    for invoice in invoices:
        face = integer(invoice["face_cents"], "invoice face")
        options = invoice["options"]
        if not options or len({p["name"] for p in options}) != len(options):
            raise ValueError("Invoice requires uniquely named payment options")
        group = []
        by_name = {}
        for p in options:
            day = delay_bound(p, "day", "latest_day")
            fee = (
                face * integer(p["fee_bps"], "discount basis points", 10000) + 9999
            ) // 10000
            fee += integer(p.get("fixed_fee_cents", 0), "fixed invoice fee", face)
            if fee > face:
                raise ValueError("Invoice fees exceed the payment")
            if invoice["id"] == maximize_discount_on:
                if len(options) != 1 or fee or minimize_source is not None:
                    raise ValueError("Use one undiscounted receipt to price an offer")
                variable_discount = column(face, cost=1, flows={day: -1})
            j = column(1, fee, flows={day: face - fee})
            group.append(j)
            by_name[p["name"]] = j
            choices.append((j, invoice["id"], p["name"], day, face - fee))
        constraints.append(({j: 1 for j in group}, 1, 1))
        if invoice.get("election_group"):
            first = grouped_elections.setdefault(invoice["election_group"], by_name)
            if first.keys() != by_name.keys():
                raise ValueError("Linked invoices require the same election choices")
            if first is not by_name:
                constraints.extend(
                    ({first[k]: 1, j: -1}, 0, 0) for k, j in by_name.items()
                )

    draws = []
    for s in sources:
        available = delay_bound(s, "available_day", "latest_available_day")
        repay = integer(s["restore_day"], "repayment or replenishment date")
        if repay <= s["available_day"]:
            raise ValueError("Repayment must follow availability")
        cap = integer(s["net_capacity_cents"], "confirmed net capacity")
        fixed = integer(s["fixed_cost_cents"], "fixed fee")
        bps = integer(s["term_cost_bps"], "all-in term cost")
        if available >= repay:
            continue
        x = column(cap, principal=1, flows={available: 1, repay: -1})
        used = column(1, fixed, flows={repay: -fixed})
        fee = column((cap * bps + 9999) // 10000, 1, flows={repay: -1})
        constraints.extend(
            [
                ({x: 1, used: -cap}, -np.inf, 0),
                ({x: 1, used: -1}, 0, np.inf),
                ({fee: 10000, x: -bps}, 0, 9999),
            ]
        )
        draws.append((s["id"], x, used, fee))
    days = sorted({0, *obligations, *(d for flow in columns for d in flow)})
    bills_to_date = list(accumulate(obligations.get(d, 0) for d in days))
    for day, due in zip(days, bills_to_date):
        flows = {
            j: sum(v for d, v in f.items() if d <= day) for j, f in enumerate(columns)
        }
        constraints.append((flows, floor + due - opening, np.inf))

    def optimize(objective):
        matrix = np.zeros((len(constraints), len(columns)))
        for i, (coeffs, _, _) in enumerate(constraints):
            for j, value in coeffs.items():
                matrix[i, j] = value
        limits = LinearConstraint(
            matrix, [r[1] for r in constraints], [r[2] for r in constraints]
        )
        result = milp(
            objective,
            integrality=np.ones(len(columns)),
            bounds=Bounds(0, caps),
            constraints=limits,
            options={"mip_rel_gap": 0.0},
        )
        if result.status == 2:
            return None
        if not result.success:
            raise RuntimeError(f"Calendar pricing failed: {result.message}")
        answer = [round(x) for x in result.x]
        if any(not 0 <= x <= cap for x, cap in zip(answer, caps)) or any(
            not lo <= sum(answer[j] * v for j, v in coeffs.items()) <= hi
            for coeffs, lo, hi in constraints
        ):
            raise ArithmeticError("Integer cash-flow reconstruction failed")
        return answer

    if maximize_discount_on is not None:
        if variable_discount is None:
            raise ValueError("Unknown receipt for the discount")
        limit = integer(fee_limit_cents, "fee limit")
        constraints.append(({j: c for j, c in enumerate(costs) if c}, 0, limit))
        objective = [0] * len(columns)
        objective[variable_discount] = -1
        answer = optimize(objective)
        if answer is None:
            return {"status": "unfunded"}
        return {"status": "funded", "max_discount_cents": answer[variable_discount]}
    if minimize_source is not None:
        if minimize_source not in {s["id"] for s in sources}:
            raise ValueError("Unknown funding source to minimize")
        limit = integer(fee_limit_cents, "fee limit")
        constraints.append(({j: c for j, c in enumerate(costs) if c}, 0, limit))
        objective = [0] * len(columns)
        for name, x, _, _ in draws:
            if name == minimize_source:
                objective[x] = 1
        minimum = optimize(objective) if columns else []
        if minimum is None:
            return {"status": "unfunded"}
        for j, weight in enumerate(objective):
            if weight:
                constraints.append(({j: 1}, minimum[j], minimum[j]))
    elif fee_limit_cents is not None:
        raise ValueError("Select a funding source when setting a fee limit")
    answer = optimize(costs) if columns else []
    if answer is None or (not columns and opening - bills_to_date[-1] < floor):
        return {"status": "unfunded"}
    cost = sum(x * c for x, c in zip(answer, costs))
    if draws:
        constraints.append(({j: c for j, c in enumerate(costs) if c}, cost, cost))
        answer = optimize(volume)
        if answer is None:
            raise ArithmeticError("Lost a previously feasible optimum")
    balances = {
        day: opening
        - due
        + sum(
            x * sum(v for d, v in f.items() if d <= day)
            for x, f in zip(answer, columns)
        )
        for day, due in zip(days, bills_to_date)
    }
    return {
        "status": "funded",
        "cost_cents": cost,
        "cash_after_settlement_cents": balances[days[-1]],
        "balances_cents": balances,
        "payments": [
            {"invoice": i, "option": p, "day": d, "receipt_cents": v}
            for j, i, p, d, v in choices
            if answer[j]
        ],
        "funding": [
            {
                "source": name,
                "net_proceeds_cents": answer[x],
                "cost_cents": answer[u] * costs[u] + answer[f],
            }
            for name, x, u, f in draws
            if answer[x]
        ],
    }


def capacity_boundary(case, source, high, above, below, benchmark):
    low = 0
    target = benchmark.get("cost_cents")
    while high - low > 1:
        capacity = (low + high) // 2
        funding = [{**row, "net_capacity_cents": capacity} if row["id"] == source else row
                   for row in case["funding"]]
        trial = price_calendar({**case, "funding": funding})
        if trial["status"] == "funded" and (target is None or trial["cost_cents"] < target):
            high, above = capacity, trial
        else:
            low, below = capacity, trial
    return {
        "minimum_useful_capacity_cents": high,
        "cost_at_threshold_cents": above["cost_cents"],
        "cost_one_cent_below_cents": below.get("cost_cents"),
    }


def explain_calendar(case):
    decision = price_calendar(case)
    alternatives = []
    for draw in decision.get("funding", []):
        source = draw["source"]
        remaining = [s for s in case["funding"] if s["id"] != source]
        alternative = price_calendar({**case, "funding": remaining})
        extra = (
            alternative["cost_cents"] - decision["cost_cents"]
            if alternative["status"] == "funded"
            else None
        )
        result = {
            "excluded_source": source,
            "best_alternative": alternative,
            "additional_cost_cents": extra,
        }
        if extra != 0:
            result.update(capacity_boundary(case, source, draw["net_proceeds_cents"], decision, alternative, alternative))

        alternatives.append(result)
    return {**decision, "alternatives": alternatives}


def main():
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Compare priced invoice elections and funding on a dated cash calendar."
    )
    parser.add_argument("case", type=Path)
    args = parser.parse_args()
    print(json.dumps(explain_calendar(json.loads(args.case.read_text())), indent=2))


if __name__ == "__main__":
    main()
