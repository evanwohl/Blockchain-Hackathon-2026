"""Replay paired diesel and oil shocks at a trip's most recently observed volatility."""

from bisect import bisect_right
from datetime import date
from fractions import Fraction
from functools import cache
from math import ceil

from freightos import backup, fill_replay


@cache
def scales(region, offsets):
    changes = fill_replay.changes(region, offsets)
    days = [r[0] for r in changes]
    result = {}
    if not days:
        return result
    for week in range((days[-1] - days[0]).days // 7 + 3):
        end = days[0] + week * backup.WEEK
        count = bisect_right(days, end)
        past = changes[max(0, count - backup.WINDOW):count]
        if len(past) < backup.WINDOW or end - past[-1][0] > 2 * backup.WEEK:
            continue

        def magnitude(column):
            recent = sum(abs(row[column]) for row in past[-8:])
            longer = sum(abs(row[column]) for row in past[-26:])
            return Fraction(recent, 16) + Fraction(longer, 52)

        result[end] = magnitude(1), magnitude(2)
    return result


def compare(check, samples, trip, offers, plan, anchor, fuel_cash, offsets, fill_day):
    if not check:
        return
    levels = scales(trip["region"], offsets)
    strikes = [Fraction(offer["strike"]) for offer in offers]
    _, payouts = fill_replay.pump_gaps([], offers, plan)
    funds = trip["budget_cents"] + trip["pump_cash_cents"] + fuel_cash
    dates = [date.fromisoformat(row[2]) for row in samples]

    def forecast(day):
        cutoff = day - 2 * backup.WEEK
        current = levels.get(cutoff)
        end = bisect_right(dates, cutoff)
        if not current or end < backup.WINDOW:
            return None
        scenarios = []
        for row, prior_day in zip(samples[end-backup.WINDOW:end], dates[end-backup.WINDOW:end]):
            prior = levels.get(prior_day - 2 * backup.WEEK)
            if not prior or prior[1] == 0 or (offers and prior[0] == 0):
                return None
            oil = anchor + row[3] * current[0] / prior[0] if offers else anchor
            diesel = max(0, trip["pump_mills"] + row[4] * current[1] / prior[1])
            bill = ceil(diesel * trip["gallons"] / 10)
            scenarios.append(max(0, bill - funds - payouts[bisect_right(strikes, oil)]))
        return backup.buffer(scenarios)

    paired = []
    for row in check["records"]:
        scaled = forecast(date.fromisoformat(row["week"]))
        if scaled is not None:
            row["scaled_cents"] = scaled
            paired.append(row)
    if not paired:
        return None
    as_of = date.fromisoformat(check["as_of"])
    fresh = fill_day - backup.WEEK <= as_of <= fill_day and check["target_cents"] is not None
    peak = max(paired, key=lambda row: row["gap_cents"] - row["scaled_cents"])
    return {"weeks": len(paired), "worst_week": peak["week"] if peak["gap_cents"] > peak["scaled_cents"] else None,
            "recent": backup.track(paired, "recent_cents"), "fixed": backup.track(paired, "fixed_cents"),
            "scaled": backup.track(paired, "scaled_cents"), "target_cents": forecast(fill_day) if fresh else None}
