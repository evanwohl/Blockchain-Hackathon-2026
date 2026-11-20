"""Weekly diesel price changes beyond the trip model's WTI sensitivity."""

import gzip
import json
from datetime import date
from fractions import Fraction
from functools import cache
from math import ceil

from freightos.funding import DATA

SNAPSHOT = DATA / "regional_diesel.json.gz"
REGIONS = {"east": "East Coast", "midwest": "Midwest", "gulf": "Gulf Coast", "rockies": "Rocky Mountain", "west": "West Coast"}
TEST_START = "2025-01-01"


def measure(rows, region, cents_per_oil_dollar):
    training, later = [], []
    for start, end in zip(rows, rows[1:]):
        days = (date.fromisoformat(end["date"]) - date.fromisoformat(start["date"])).days
        if days != 7:
            continue
        pump_change = Fraction(end["diesel_mills"][region] - start["diesel_mills"][region], 10)
        oil_change = Fraction(end["oil_cents"] - start["oil_cents"], 100)
        error = pump_change - oil_change * cents_per_oil_dollar
        if end["date"] < TEST_START:
            training.append(error)
        elif start["date"] >= TEST_START:
            later.append((end["date"], error))
    if not training or not later:
        raise ValueError("Insufficient weekly diesel observations")
    rank = ceil(Fraction(9 * len(training), 10))
    allowance = max(0, ceil(sorted(training)[rank - 1]))
    worst_day, worst = max(later, key=lambda row: row[1])
    return {"region": region, "name": REGIONS[region], "cents_per_gallon": allowance,
            "training_weeks": len(training), "test_weeks": len(later), "test_end": later[-1][0],
            "oil_only_misses": sum(error > 0 for _, error in later),
            "allowance_misses": sum(error > allowance for _, error in later),
            "worst_miss_cents": max(0, ceil(worst - allowance)), "worst_week": worst_day}


@cache
def history(region, cents_per_oil_dollar):
    if region not in REGIONS:
        raise ValueError("Unknown diesel region")
    return measure(json.loads(gzip.decompress(SNAPSHOT.read_bytes()))["prices"], region, cents_per_oil_dollar)
