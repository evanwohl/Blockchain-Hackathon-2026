"""Price a shared fuel reserve across weekly stops before the first invoice pays."""

from bisect import bisect_right
from datetime import date

from freightos import backup, fill_replay


def paths(samples, trip, prices, reserve, payouts, stops, cutoff, *, payout_stop=1):
    runs = []
    for band, _, stamp, *_ in samples:
        first = date.fromisoformat(stamp)
        end = first + (stops - 1) * backup.WEEK
        if end > cutoff:
            break
        start = prices.get((first - backup.WEEK).isoformat())
        levels = [prices.get((first + i * backup.WEEK).isoformat()) for i in range(stops)]
        if start is None or None in levels:
            continue
        balance = reserve
        needs = []
        for stop, level in enumerate(levels, 1):
            if stop == payout_stop:
                balance += payouts[band]
            pump = max(0, trip["pump_mills"] + level - start)
            bill = (pump * trip["gallons"] + 9) // 10
            balance += trip["budget_cents"] + trip["pump_cash_cents"] - bill
            needs.append(max(0, -balance))
        runs.append((first, end, needs))
    return runs


def coverage_bounds(runs):
    needs = [row[2] for row in runs]
    columns = list(zip(*needs, strict=True))
    leaders = [sorted(col, reverse=True)[:2] for col in columns]
    alternatives = {tuple(second if path[i] == first else first for i, (first, second) in enumerate(leaders))
                    for path in needs}
    return sorted(a for a in alternatives if not any(b != a and all(y <= x for x, y in zip(a, b)) for b in alternatives))


def estimate(samples, trip, reserve, offers, plan, as_of, days, *, payout_stop=1):
    stops = (days + 6) // 7
    _, payouts = fill_replay.pump_gaps([], offers, plan)
    prices = fill_replay.prices()["diesel_mills"][trip["region"]]
    runs = paths(samples, trip, prices, reserve, payouts, stops, as_of - backup.WEEK, payout_stop=payout_stop)
    if len(runs) < backup.WINDOW:
        return None
    ends = [r[1] for r in runs]
    first_dates = [date.fromisoformat(row[2]) for row in samples]
    first_gaps, _ = fill_replay.pump_gaps(samples, offers, plan)
    if payout_stop > 1:
        first_gaps = [row[1] for row in samples]
    records = []
    eligible_from = backup.EVALUATION_START + backup.WEEK
    for first, last, needs in runs:
        if first < eligible_from:
            continue
        cutoff = first - 2 * backup.WEEK
        end = bisect_right(ends, cutoff)
        one_end = bisect_right(first_dates, cutoff)
        if end < backup.WINDOW or one_end < backup.WINDOW:
            continue
        prior = runs[end-backup.WINDOW:end]
        records.append({"first": first.isoformat(), "last": last.isoformat(), "trained_through": ends[end-1].isoformat(),
                        "gap_cents": max(needs), "need_cents": needs, "bounds": coverage_bounds(prior),
                        "target_cents": backup.buffer([max(r[2]) for r in prior]),
                        "single_cents": backup.buffer(first_gaps[one_end-backup.WINDOW:one_end])})
        eligible_from = last + backup.WEEK
    recent = runs[-backup.WINDOW:]
    fresh = as_of - ends[-1] < 2 * backup.WEEK
    return {"days": days, "stops": stops, "payout_stop": payout_stop, "through": ends[-1].isoformat(), "window": len(recent),
            "reserve_cents": reserve, "weekly_cash_cents": trip["budget_cents"] + trip["pump_cash_cents"],
            "gallons": trip["gallons"], "premium_cents": plan.get("upfront_cents", 0),
            "target_cents": backup.buffer([max(r[2]) for r in recent]) if fresh else None,
            "bounds": coverage_bounds(recent) if fresh else [],
            "recent": [{"first": r[0].isoformat(), "need_cents": r[2]} for r in recent] if fresh else [],
            "records": records, "multi": backup.track(records, "target_cents") if records else None,
            "single": backup.track(records, "single_cents") if records else None}

def hedge_paths(samples, trip, reserve, offers, stops, cutoff):
    bands = {row[2]: row[0] for row in samples}
    prices = fill_replay.prices()["diesel_mills"][trip["region"]]
    cash = paths(samples, trip, prices, reserve, [0] * (len(offers) + 1), stops, cutoff)
    return [(bands[first.isoformat()], max(need), need[0], first.isoformat(), last.isoformat())
            for first, last, need in cash]


def separate_stretches(rows):
    chosen = []
    for row in rows:
        if not chosen or chosen[-1][4] < row[3]:
            chosen.append(row)
    return chosen


def hedge_cash(rows, payouts, premium):
    ready, delayed = [], []
    for band, peak, first, *_ in rows:
        remaining = max(0, peak - payouts[band])
        ready.append(premium + remaining)
        delayed.append(premium + max(first, remaining))
    def hard(values):
        return (fill_replay.tail_sum(values) + len(values) - 1) // len(values)
    return {"on_time_cents": hard(ready), "late_cents": hard(delayed),
            "mean_late_cents": (sum(delayed) + len(delayed) - 1) // len(delayed)}


def hedge_replay(training, later, stops, offers, plans):
    sample = separate_stretches(later)
    if len(sample) < 10:
        return None
    results = {}
    for name, plan in plans.items():
        _, payouts = fill_replay.pump_gaps([], offers, plan)
        premium = plan["upfront_cents"]
        results[name] = {"premium_cents": premium, "payouts_by_band": payouts,
                         "training": hedge_cash(training, payouts, premium), **hedge_cash(sample, payouts, premium)}
    shifts = []
    for start in range(stops):
        rows = separate_stretches(later[start:])
        if len(rows) < 10:
            continue
        scores = {name: hedge_cash(rows, result["payouts_by_band"], result["premium_cents"])["late_cents"]
                  for name, result in results.items()}
        shifts.append({"first": rows[0][3], "count": len(rows), **scores})
    return {"training_count": len(training), "trained_through": training[-1][4], "count": len(sample),
            "scores": results, "strikes": [o["strike"] for o in offers], "starts": shifts,
            "columns": ["winning_strikes", "peak_gap_cents", "first_gap_cents", "first_fill", "last_fill"],
            "training_rows": training, "rows": sample}
