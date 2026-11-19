from collections import defaultdict


def reserve_deadline(calendar, payments, draws, source_id):
    chosen = next((draw for draw in draws if draw["source"] == source_id), None)
    if not chosen:
        return None
    sources = {s["id"]: s for s in calendar["funding"]}
    reserve = sources[source_id]
    arrival = reserve.get("latest_available_day", reserve["available_day"])
    maturity = reserve["restore_day"]
    flows = defaultdict(int)
    for p in payments:
        flows[p["day"]] += p["receipt_cents"]
    for b in calendar["bills"]:
        flows[b.get("earliest_day", b["day"])] -= b.get("max_cents", b["cents"])
    for draw in draws:
        terms = sources[draw["source"]]
        if draw["source"] != source_id:
            flows[terms.get("latest_available_day", terms["available_day"])] += draw["net_proceeds_cents"]
        flows[terms["restore_day"]] -= draw["net_proceeds_cents"] + draw["cost_cents"]
    balance = calendar["opening_cash_cents"]
    floor = calendar["reserve_floor_cents"]
    for day, flow in sorted(flows.items()):
        balance += flow
        if balance < floor:
            if day < arrival:
                raise ValueError("The selected plan misses an obligation before reserve availability")
            return {"arrival_day": arrival, "last_day": min(day, maturity - 1),
                    "first_short_day": day, "shortfall_cents": floor - balance}
    return {"arrival_day": arrival, "last_day": maturity - 1, "first_short_day": None, "shortfall_cents": 0}
