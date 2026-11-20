"""Price useful hedge payouts against the same cash budget through payday."""

from datetime import date
from fractions import Fraction
from math import ceil

from freightos import backup, fill_replay


def funding_need(path, payouts):
    band, peak, first, *_ = path
    return max(first, peak - payouts[band])


def price_limit(rows, plans, stops, first_fill):
    cutoff = first_fill - 2 * backup.WEEK
    start = (cutoff - 104 * backup.WEEK).isoformat()
    eligible = [r for r in rows if r[3] >= start and r[4] <= cutoff.isoformat()]
    if not eligible or date.fromisoformat(eligible[-1][4]) <= cutoff - 2 * backup.WEEK:
        return None
    metrics = ("average", "hard")
    margins = {metric: {} for metric in metrics}
    cohorts = []
    for shift in range(stops):
        sample = []
        for index in range(len(eligible) - 1 - shift, -1, -1):
            row = eligible[index]
            if not sample or row[4] < sample[-1][3]:
                sample.append(row)
        if len(sample) < 10:
            return None
        cohorts.append([r[3] for r in reversed(sample)])
        cash = [r[1] for r in sample]
        for name, plan in plans.items():
            if name == "cash":
                continue
            needs = [funding_need(r, plan["payouts_by_band"]) for r in sample]
            gross = (sum(cash) - sum(needs), fill_replay.tail_sum(cash) - fill_replay.tail_sum(needs))
            for metric, numerator in zip(metrics, gross, strict=True):
                value = Fraction(numerator, len(sample))
                prior = margins[metric].get(name)
                if prior is None or value < prior:
                    margins[metric][name] = value
    result = {}
    for metric, gross in margins.items():
        chosen = "cash"
        best = 0
        limits = {}
        for name, value in gross.items():
            limits[name] = ceil(value) - 1 if value > 0 else None
            net = value - plans[name]["premium_cents"]
            if net > best or (net == best and best > 0 and plans[name]["premium_cents"] < plans[chosen]["premium_cents"]):
                chosen, best = name, net
        result[metric] = {"choice": chosen, "max_price_cents": limits}
    return {"through": eligible[-1][4], "cohorts": cohorts, **result}


def price_policy(training, later, replay, stops, next_fill, as_of):
    rows = [*training, *later]
    plans = replay["scores"]
    records = []
    for path in replay["rows"]:
        day = date.fromisoformat(path[3])
        if training[-1][4] > (day - 2 * backup.WEEK).isoformat():
            continue
        limits = price_limit(rows, plans, stops, day)
        if limits is None:
            continue
        used = {name: funding_need(path, plan["payouts_by_band"]) + plan["premium_cents"] for name, plan in plans.items()}
        records.append({"first": path[3], "last": path[4], "through": limits["through"], "used_cents": used,
                        "average": limits["average"], "hard": limits["hard"]})
    summary = {}
    for rule in ("average", "hard", *plans):
        values = [r["used_cents"][r[rule]["choice"] if rule in ("average", "hard") else rule] for r in records]
        if values:
            summary[rule] = {"average_cents": ceil(Fraction(sum(values), len(values))),
                             "hard_cents": ceil(Fraction(fill_replay.tail_sum(values), len(values)))}
    current = None
    if next_fill - backup.WEEK <= as_of <= next_fill and training[-1][4] <= (next_fill - 2 * backup.WEEK).isoformat():
        current = price_limit(rows, plans, stops, next_fill)
    return {"first_fill": next_fill.isoformat(), "current": current, "records": records, "summary": summary, "later_paths": later}
