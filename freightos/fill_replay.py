"""Apply paired weekly oil and diesel changes to one proposed fill."""

from bisect import bisect_right
from datetime import date, timedelta
from fractions import Fraction
from functools import cache
from itertools import accumulate
from math import ceil
import gzip
import json

import numpy as np

from freightos import regional_diesel, replay_prices


@cache
def prices():
    return json.loads(gzip.decompress(replay_prices.SNAPSHOT.read_bytes()))


def expiry_offsets(reference, expiry):
    days = (expiry - reference).days
    to_fill = (0 - expiry.weekday()) % 7
    if 0 < days and days + to_fill <= 7:
        return days + to_fill, to_fill


@cache
def changes(region, offsets):
    tape = prices()
    oil = tape["oil_cents"]
    diesel = tape["diesel_mills"][region]
    weeks = []
    for stamp, price in sorted(diesel.items()):
        end = date.fromisoformat(stamp)
        start = (end - timedelta(days=7)).isoformat()
        if end.weekday() != 0 or start not in diesel:
            continue
        oil_move = Fraction(0)
        if offsets:
            opening, closing = ((end - timedelta(days=n)).isoformat() for n in offsets)
            if opening not in oil or closing not in oil:
                continue
            oil_move = Fraction(oil[closing] - oil[opening], 100)
        weeks.append((end, oil_move, price - diesel[start]))
    return weeks


def fills(trip, anchor, offers, fuel_cash, as_of, offsets):
    strikes = [Fraction(o["strike"]) for o in offers]
    early, later = [], []
    split = date.fromisoformat(regional_diesel.TEST_START)
    cutoff = as_of - timedelta(days=7)
    for end, oil_move, diesel_move in changes(trip["region"], offsets):
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


def delay_threshold(ready, delayed, premium):
    """Fewest adversarial payout delays that erase the tail-cash saving against cash alone."""
    weeks = len(ready)
    whole, remainder = divmod(weeks, 10)
    fractional = int(remainder > 0)
    max_delays = whole + fractional
    scores = np.full((whole + 1, fractional + 1, max_delays + 1), -10**15, dtype=np.int64)
    scores[0, 0, :] = 0
    for on_time, late in zip(ready, delayed):
        next_scores = scores.copy()
        for cost, used in ((on_time, 0), (late, 1)):
            prior = scores[:, :, :max_delays + 1 - used]
            full = next_scores[1:, :, used:]
            np.maximum(full, prior[:-1] + 10*cost, out=full)
            if fractional:
                part = next_scores[:, 1, used:]
                np.maximum(part, prior[:, 0] + remainder*cost, out=part)
        scores = next_scores
    totals = scores[whole, fractional] + weeks*premium
    count = int(np.flatnonzero(totals >= tail_sum(delayed))[0])
    return {"count": count, "tail_cash_cents": ceil(Fraction(int(totals[count]), weeks))}


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
