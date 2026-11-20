"""Value incremental Kamino collateral shares across matched fuel-loan scenarios."""
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from functools import cache
import gzip
import hashlib
import json
from math import ceil, floor
from urllib.parse import urlencode

from freightos import mainnet_loan, pledge_topup

EVIDENCE = mainnet_loan.EVIDENCE.with_name("collateral_yield.json.gz")
DIGEST = "6a116b0b027f33441d66f564bb656c10d5bb6b0286443158822f772c1fd1deb7"
SOURCE = f"https://api.kamino.finance/kamino-market/{mainnet_loan.MARKET}/reserves/{mainnet_loan.SOL_RESERVE}/metrics/history"


def observations(records):
    rates = {}
    for record in records:
        response = record["response"]
        hourly, = response["history"]
        start = datetime.fromisoformat(hourly["timestamp"])
        end = start + timedelta(hours=1)
        params = dict(start=start.isoformat().replace("+00:00", "Z"), end=end.isoformat().replace("+00:00", "Z"), env="mainnet-beta")
        metrics = hourly["metrics"]
        rate = Fraction(metrics["exchangeRate"])
        if (record["url"] != SOURCE + "?" + urlencode(params) or response["reserve"] != mainnet_loan.SOL_RESERVE
                or metrics["mintAddress"] != str(mainnet_loan.credit.SOL) or metrics["decimals"] != 9
                or metrics["status"] != "Active" or rate <= 0 or end in rates):
            raise ValueError("Kamino collateral history could not be matched to the reserve and hour")
        rates[end] = rate
    return rates


@cache
def saved():
    raw = EVIDENCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise ValueError("The recorded SOL share history changed")
    return observations(json.loads(gzip.decompress(raw)))


def compare(topup, sale, original, terms, path, price, stress_floor, strict):
    rates = saved()
    if stress_floor is not None:
        stress_floor = Fraction(stress_floor)
    supply = terms["collateral_shares"]
    backing = Fraction(terms["collateral_supply_sf"], 1 << 60)
    first, debit = pledge_topup.deposited(original, backing, supply)
    supply += first
    backing += debit
    initial = first * backing / supply
    after = (first + topup["extra_shares"]) * (backing + topup["deposit_lamports"]) / (supply + topup["extra_shares"])
    reduction = topup["payday_cents"] - sale["payday_cents"]
    paths = []
    limits = []
    for start, trace in sorted(path["traces"].items()):
        points = dict(trace)
        if 30 not in points or 60 not in points:
            continue
        opening = datetime.fromisoformat(start).replace(hour=21, tzinfo=timezone.utc)
        middle, end = opening + timedelta(days=15), opening + timedelta(days=30)
        growth = rates[middle] / rates[end]
        excess = floor(after * growth) - floor(initial * growth) - topup["deposit_lamports"]
        mark = price * points[60] / points[30]
        paths.append((start, end.date().isoformat(), excess, mark))
        limits.append(floor((reduction-1) * mainnet_loan.LAMPORTS / mark) - excess)
    _, last_end, last_excess, _ = paths[-1]
    if stress_floor is not None:
        limits.append(floor((reduction-1) * mainnet_loan.LAMPORTS / stress_floor) - last_excess)
    strict_cap = min(sale["max_lamports"], *limits)
    cap = strict_cap if strict else sale["max_lamports"]
    if cap <= 0:
        return dict(strict_cap_lamports=0)
    histories = [dict(start=start, end=end, extra_lamports=excess,
                      difference_cents=floor(reduction - (cap + excess) * mark / mainnet_loan.LAMPORTS))
                 for start, end, excess, mark in paths]
    units = last_excess + cap
    result = dict(histories=histories, end=last_end, extra_lamports=last_excess, strict_cap_lamports=max(0, strict_cap),
                  crossover_cents=ceil(Fraction(reduction * mainnet_loan.LAMPORTS, units)) if units > 0 else None)
    if stress_floor is not None:
        result["stress_difference_cents"] = floor(reduction - units * stress_floor / mainnet_loan.LAMPORTS)
    return result
