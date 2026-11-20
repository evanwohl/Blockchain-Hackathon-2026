"""Apply paired weekly oil and diesel changes to one proposed fill."""

from bisect import bisect_right
from datetime import date, timedelta
from fractions import Fraction
from functools import cache
from itertools import accumulate
from math import ceil
import gzip
import json

from freightos import regional_diesel


@cache
def changes(region):
    rows = json.loads(gzip.decompress(regional_diesel.SNAPSHOT.read_bytes()))["prices"]
    weeks = []
    for a, b in zip(rows, rows[1:]):
        end = date.fromisoformat(b["date"])
        if end - date.fromisoformat(a["date"]) == timedelta(days=7):
            weeks.append((end, Fraction(b["oil_cents"] - a["oil_cents"], 100),
                          b["diesel_mills"][region] - a["diesel_mills"][region]))
    return weeks


def fills(trip, anchor, offers, fuel_cash, as_of):
    strikes = [Fraction(o["strike"]) for o in offers]
    early, later = [], []
    split = date.fromisoformat(regional_diesel.TEST_START)
    cutoff = as_of - timedelta(days=7)
    for end, oil_move, diesel_move in changes(trip["region"]):
        if end > cutoff or end - timedelta(days=7) < split <= end:
            continue
        oil = anchor + oil_move
        pump = max(0, trip["pump_mills"] + diesel_move)
        bill = (pump * trip["gallons"] + 9) // 10
        missing = max(0, bill - trip["budget_cents"] - fuel_cash - trip["pump_cash_cents"])
        group = early if end < split else later
        group.append((bisect_right(strikes, oil), missing, end.isoformat(), oil_move, diesel_move, bill))
    return early, later


def tail_sum(values):
    ordered = sorted(values, reverse=True)
    whole, remainder = divmod(len(ordered), 10)
    return 10 * sum(ordered[:whole]) + (remainder * ordered[whole] if remainder else 0)


def pump_gaps(samples, offers, plan):
    positions = {leg["strike"]: leg["lots"] for leg in plan["legs"]}
    payouts = list(accumulate((positions.get(o["strike"], 0) for o in offers), initial=0))
    return [max(0, need - payouts[band]) for band, need, *_ in samples], payouts


def month_sensitivity(samples, offers, original, fitted):
    months = [week[:7] for _, _, week, *_ in samples]
    original_gaps, _ = pump_gaps(samples, offers, original)
    fitted_gaps, _ = pump_gaps(samples, offers, fitted)
    premium_change = fitted["upfront_cents"] - original["upfront_cents"]
    changes = []
    for month in sorted(set(months)):
        before = [gap for gap, m in zip(original_gaps, months) if m != month]
        after = [gap for gap, m in zip(fitted_gaps, months) if m != month]
        if before:
            changes.append(premium_change + Fraction(tail_sum(after) - tail_sum(before), len(before)))
    if changes:
        return {"months": len(changes), "lower": sum(change < 0 for change in changes)}


def summary(samples, offers, plan):
    gaps, payouts = pump_gaps(samples, offers, plan)
    peak = max(range(len(gaps)), key=gaps.__getitem__)
    band, need, end, oil, diesel, bill = samples[peak]
    return {"weeks": len(gaps), "short_weeks": sum(g > 0 for g in gaps),
            "tail_cash_cents": plan["upfront_cents"] + ceil(Fraction(tail_sum(gaps), len(gaps))),
            "premium_cents": plan["upfront_cents"], "end": samples[-1][2],
            "peak": {"week": end, "short_cents": gaps[peak], "bill_cents": bill, "oil_move_cents": int(100*oil),
                     "diesel_move_mills": diesel, "payout_cents": payouts[band], "before_payout_cents": need}}
