"""Walk-forward estimates of the cash still needed at a fuel stop."""

from bisect import bisect_right
from datetime import date, timedelta

WINDOW = 26
EVALUATION_START = date(2026, 1, 1)
WEEK = timedelta(days=7)


def buffer(gaps):
    rank = (9 * (len(gaps) + 1) + 9) // 10
    return sorted(gaps)[min(len(gaps), rank) - 1]


def track(records, field):
    gaps = [row["gap_cents"] - row[field] for row in records]
    return {"misses": sum(gap > 0 for gap in gaps),
            "mean_cents": (sum(row[field] for row in records) + len(gaps) - 1) // len(gaps),
            "worst_uncovered_cents": max(0, max(gaps))}


def estimate(samples, gaps, as_of):
    cutoff = as_of - WEEK
    observations = [(date.fromisoformat(row[2]), gap) for row, gap in zip(samples, gaps, strict=True)]
    days = [day for day, _ in observations]
    usable = bisect_right(days, cutoff)
    days = days[:usable]
    values = [gap for _, gap in observations[:usable]]
    prior = bisect_right(days, EVALUATION_START - WEEK)
    if prior < WINDOW:
        return None
    fixed = buffer(values[:prior])
    records = []
    for day, gap in zip(days, values):
        start = day - WEEK
        if start < EVALUATION_START:
            continue
        end = bisect_right(days, start - WEEK)
        records.append({"week": day.isoformat(), "through": days[end-1].isoformat(), "gap_cents": gap,
                        "recent_cents": buffer(values[end-WINDOW:end]), "fixed_cents": fixed})
    if not records:
        return None
    return {"as_of": as_of.isoformat(), "through": days[-1].isoformat(), "window": WINDOW,
            "target_cents": buffer(values[-WINDOW:]) if as_of - days[-1] < 2*WEEK else None,
            "fixed_cents": fixed, "fixed_weeks": prior, "fixed_through": days[prior-1].isoformat(),
            "recent": track(records, "recent_cents"), "fixed": track(records, "fixed_cents"), "records": records}
