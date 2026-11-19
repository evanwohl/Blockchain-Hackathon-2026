from collections import defaultdict


def _daily_flows(calendar, payments, draws, *, withhold=None, delay_invoice=None, delay_days=0):
    sources = {s["id"]: s for s in calendar["funding"]}
    flows = defaultdict(int)
    for payment in payments:
        day = payment["day"]
        if delay_invoice is not None and payment["invoice"] == delay_invoice and payment["option"] == "standard":
            day += delay_days
        flows[day] += payment["receipt_cents"]
    for b in calendar["bills"]:
        flows[b.get("earliest_day", b["day"])] -= b.get("max_cents", b["cents"])
    for draw in draws:
        terms = sources[draw["source"]]
        if draw["source"] != withhold:
            flows[terms.get("latest_available_day", terms["available_day"])] += draw["net_proceeds_cents"]
        flows[terms["restore_day"]] -= draw["net_proceeds_cents"] + draw["cost_cents"]
    return sorted(flows.items())


def late_invoice_calendar(calendar, invoice, days):
    invoices = [{**item, "options": [
        {**option, "latest_day": option.get("latest_day", option["day"]) + days}
        if item["id"] == invoice and option["name"] == "standard" else option for option in item["options"]
    ]} for item in calendar["invoices"]]
    return {**calendar, "invoices": invoices}


def invoice_delay_gap(calendar, payments, draws, invoice, days):
    balance = calendar["opening_cash_cents"] - calendar["reserve_floor_cents"]
    largest = 0
    first = None
    for day, flow in _daily_flows(calendar, payments, draws, delay_invoice=invoice, delay_days=days):
        balance += flow
        if balance < 0:
            if first is None:
                first = day
            largest = max(largest, -balance)
    return {"first_short_day": first, "extra_cash_cents": largest}


def reserve_deadline(calendar, payments, draws, source_id):
    chosen = next((draw for draw in draws if draw["source"] == source_id), None)
    if not chosen:
        return None
    reserve = next(s for s in calendar["funding"] if s["id"] == source_id)
    arrival = reserve.get("latest_available_day", reserve["available_day"])
    maturity = reserve["restore_day"]
    balance = calendar["opening_cash_cents"]
    floor = calendar["reserve_floor_cents"]
    for day, flow in _daily_flows(calendar, payments, draws, withhold=source_id):
        balance += flow
        if balance < floor:
            if day < arrival:
                raise ValueError("The selected plan misses an obligation before reserve availability")
            return {"arrival_day": arrival, "last_day": min(day, maturity - 1),
                    "first_short_day": day, "shortfall_cents": floor - balance}
    return {"arrival_day": arrival, "last_day": maturity - 1, "first_short_day": None, "shortfall_cents": 0}
