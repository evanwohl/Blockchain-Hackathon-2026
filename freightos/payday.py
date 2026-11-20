"""Price a shared fuel reserve across weekly stops before the first invoice pays."""

from bisect import bisect_right
from datetime import date

from freightos import backup, fill_replay


def paths(samples, trip, prices, reserve, payouts, stops, cutoff):
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
        balance = reserve + payouts[band]
        needs = []
        for level in levels:
            pump = max(0, trip["pump_mills"] + level - start)
            bill = (pump * trip["gallons"] + 9) // 10
            balance += trip["budget_cents"] + trip["pump_cash_cents"] - bill
            needs.append(max(0, -balance))
        runs.append((first, end, needs))
    return runs


def estimate(samples, trip, reserve, offers, plan, as_of, days):
    stops = (days + 6) // 7
    _, payouts = fill_replay.pump_gaps([], offers, plan)
    prices = fill_replay.prices()["diesel_mills"][trip["region"]]
    runs = paths(samples, trip, prices, reserve, payouts, stops, as_of - backup.WEEK)
    if len(runs) < backup.WINDOW:
        return None
    ends = [r[1] for r in runs]
    first_dates = [date.fromisoformat(row[2]) for row in samples]
    first_gaps, _ = fill_replay.pump_gaps(samples, offers, plan)
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
                        "gap_cents": max(needs), "target_cents": backup.buffer([max(r[2]) for r in prior]),
                        "single_cents": backup.buffer(first_gaps[one_end-backup.WINDOW:one_end])})
        eligible_from = last + backup.WEEK
    recent = runs[-backup.WINDOW:]
    fresh = as_of - ends[-1] < 2 * backup.WEEK
    return {"days": days, "stops": stops, "through": ends[-1].isoformat(), "window": len(recent),
            "reserve_cents": reserve, "weekly_cash_cents": trip["budget_cents"] + trip["pump_cash_cents"],
            "gallons": trip["gallons"], "premium_cents": plan.get("upfront_cents", 0),
            "target_cents": backup.buffer([max(r[2]) for r in recent]) if fresh else None,
            "recent": [{"first": r[0].isoformat(), "need_cents": r[2]} for r in recent] if fresh else [],
            "records": records, "multi": backup.track(records, "target_cents") if records else None,
            "single": backup.track(records, "single_cents") if records else None}
