"""Replay matched Gemini settlement paths against a Kamino SOL pledge."""
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from functools import cache

from freightos import payday_policy
from freightos.freight_credit import ceiling

EVIDENCE = payday_policy.HISTORY.with_name("sol_collateral_paths.json.gz")
DIGEST = "e02ea92fff6701d29d60120d59eab90b2eb49f8c256358453f0c1c6d3be10292"


def verify(raw):
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise ValueError("The Gemini collateral path evidence changed")
    events = json.loads(raw)
    windows, _ = payday_policy.history()
    extremes, traces = {}, {}
    verified, missed, longest_gap = set(), set(), 0
    for dates, endpoint in windows:
        start = datetime.fromisoformat(dates["start"]).replace(hour=21, tzinfo=timezone.utc)
        source, initial = payday_policy.settlement(events[start.strftime("SOL%y%m%d%H%M")], start)
        trace = []
        previous = 0
        for step in range(61):
            when = start + timedelta(hours=12 * step)
            ticker = when.strftime("SOL%y%m%d%H%M")
            event = events[ticker]
            try:
                index, value = payday_policy.settlement(event, when)
            except (KeyError, ValueError):
                index = None
            if index != source:
                if step in (0, 60):
                    raise ValueError("Gemini path endpoint is unavailable")
                missed.add(ticker)
                continue
            verified.add(ticker)
            ratio = value / initial
            trace.append((step, ratio))
            if step == 60 and ratio != endpoint:
                raise ValueError("Gemini path does not match the payday evidence")
            longest_gap = max(longest_gap, 12 * (step - previous))
            previous = step
            if step not in extremes or ratio < extremes[step][0]:
                extremes[step] = (ratio, ticker, dates["start"])
        traces[dates["start"]] = trace
    return dict(extremes=extremes, traces=traces, windows=len(windows), observations=len(verified),
                omitted=len(missed), max_gap_hours=longest_gap)


@cache
def saved():
    return verify(gzip.decompress(EVIDENCE.read_bytes()))


def accrual(annual_rate, hours):
    return (annual_rate * hours / (365 * 24)).exp()


def barriers(path, spot, apr):
    retained = []
    for step, (ratio, ticker, start) in sorted(path["extremes"].items(), reverse=True):
        cents = int(Fraction(spot) * ratio * 100)
        if cents <= 0:
            raise ValueError("The collateral price is below the supported precision")
        # A later mark at the same or lower price dominates for any positive debt at a nonnegative rate.
        if not retained or cents < retained[-1]["price_cents"]:
            retained.append(dict(hours=12 * step, price_cents=cents, ticker=ticker, start=start,
                                 accrual=accrual(apr, 12 * step)))
    return retained


def peak(debt_raw, marks):
    return max(((Decimal(ceiling(debt_raw * m["accrual"])) / m["price_cents"], m) for m in marks),
               key=lambda pair: pair[0])

def replay(path, marks, debt, pledge, lending):
    _, witness = peak(debt, marks)
    start = witness["start"]
    conversion = lending["usd_price"] * lending["factor"] * 10**7 / pledge
    samples = []
    for step, ratio in path["traces"][start]:
        cents = int(Fraction(lending["sol_price"]) * ratio * 100)
        accrued = ceiling(debt * accrual(2 * lending["apr"], 12 * step))
        samples.append(dict(hours=step * 12, price_cents=cents, ltv_bps=ceiling(accrued * conversion / cents)))
    worst = next(p for p in samples if p["hours"] == witness["hours"])
    return dict(peak_ltv_bps=worst["ltv_bps"], hours=witness["hours"], price_cents=witness["price_cents"],
                start=start, ticker=witness["ticker"], samples=samples)
