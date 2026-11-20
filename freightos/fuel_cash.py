"""Compare two ways to split a fuel bill between the wallet and separate cash."""
import argparse
import gzip
import json
from functools import cache
from hashlib import sha256
from pathlib import Path

from freightos import payday_policy, reserve_credit, stop_backup
from hedging.quick_pay import integer

SAMPLE = stop_backup.SAMPLE.with_name("fuel_cash_choice.json.gz")
SAMPLE_CASH = 7500


@cache
def saved():
    raw = gzip.decompress(SAMPLE.read_bytes())
    if sha256(raw).hexdigest() != "f71fd29dc9cb1211503671639a8dfb9edfe07da4fe26b2950efb3c3c69629021":
        raise ValueError("The cash comparison evidence changed")
    return json.loads(raw)["result"]


def payment(quote, route):
    scale = quote["scale"]
    vendor = route["vendor_raw"] // scale if route else 0
    separate = quote["bill_cents"] - vendor
    restored_cash = (route["reserve_raw"] + route["wallet_raw"]) // scale if route else 0
    return {"separate_cents": separate, "untouched_cents": quote["separate_cents"] - separate, "vendor_cents": vendor,
            "reserve_cents": route["reserve_raw"] // scale if route else 0,
            "wallet_cash_cents": route["wallet_raw"] // scale if route else 0,
            "retained_cents": route["retained_raw"] // scale if route else None,
            "exchange_cents": route["swap_raw"] // scale if route else 0,
            "principal_cents": reserve_credit.INVOICE_CENTS - separate - restored_cash,
            "restore_lamports": route["limit_lamports"] + route["fee_lamports"] if route else 0}


def cash_left(plan, price, denominator):
    return plan["principal_cents"] - (plan["restore_lamports"] * price + denominator - 1) // denominator


def advantage(left, right, denominator, low, high):
    gap, price = payday_policy.worst_rounding_gap(right["restore_lamports"], left["restore_lamports"], denominator, low, high)
    return left["principal_cents"] - right["principal_cents"] + gap, price


def lasting_advantage(first, second, denominator):
    delta = first["restore_lamports"] - second["restore_lamports"]
    if delta <= 0:
        return None
    difference = first["principal_cents"] - second["principal_cents"]
    # Above this upper bound the unrounded gap is already at least one whole cent.
    high = max(0, ((difference + 1) * denominator + delta - 1) // delta)
    low, last = 0, high
    while low < high:
        mid = (low + high) // 2
        gap, _ = advantage(first, second, denominator, mid, last)
        if gap >= 0:
            low = mid + 1
        else:
            high = mid
    return low


def compare(quote, prices):
    available = quote["separate_cents"]
    minimum = payment(quote, quote["best"])
    choices = []
    if minimum["separate_cents"] <= available:
        choices.append(minimum)
    candidates = quote["fixed_routes"]
    if available >= quote["bill_cents"] and minimum["vendor_cents"]:
        choices.append(payment(quote, None))
    elif candidates:
        choices.append(payment(quote, min(candidates, key=lambda q: q["limit_lamports"] + q["fee_lamports"])))
    low, high = min(prices), max(prices)
    denominator = quote["scale"] * 100_000
    for plan in choices:
        plan["least_cents"] = cash_left(plan, high, denominator)
        plan["most_cents"] = cash_left(plan, low, denominator)
        gap, witness = max((advantage(r, plan, denominator, low, high) for r in choices if r is not plan), default=(0, low))
        plan.update(regret_cents=max(0, gap), witness_cents=witness, minimum_edge_cents=-gap if len(choices) > 1 else None)
    threshold = lasting_advantage(*choices, denominator) if len(choices) == 2 else None
    return {"choices": sorted(choices, key=lambda p: (p["regret_cents"], -p["least_cents"], p["separate_cents"])),
            "lasting_price_cents": threshold, "low_price_cents": low, "high_price_cents": high}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, help="Downloaded Solana cash split checks")
    parser.add_argument("--gemini-evidence", type=Path, help="Downloaded Gemini settlement history")
    parser.add_argument("--stress-cents", type=int, help="Additional all-in SOL price in cents")
    args = parser.parse_args()
    if args.stress_cents is not None:
        integer(args.stress_cents, "additional SOL price", 10000000)
    if args.gemini_evidence:
        payday_policy.verify_history(args.gemini_evidence.read_bytes())
    raw = args.evidence.read_bytes() if args.evidence else gzip.decompress(SAMPLE.read_bytes())
    checked = stop_backup.replay(json.loads(raw))
    reference = payday_policy.reference_price(payday_policy.recorded_menu()[0])
    cases, _ = payday_policy.price_cases(reference, args.stress_cents)
    print(json.dumps(compare(checked, [c["price_cents"] for c in cases]), indent=2))
