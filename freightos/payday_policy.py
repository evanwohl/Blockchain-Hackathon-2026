"""Compare funding elections against matched 30-day Gemini SOL settlement moves."""
import argparse
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from fractions import Fraction
from functools import cache
from pathlib import Path

from freightos import customer_credit, devnet, fuel_swap, load_credit, reserve_credit, stop_backup
from hedging.quick_pay import integer

HISTORY = Path(__file__).with_name("artifacts") / "gemini_ladder" / "sol_payday.json.gz"
HISTORY_HASH = "0a8363891392de7655aeb3a3a2159c323a54f58adcabd3dd3d3adb6e6160125e"
LAST = datetime(2026, 11, 19, 21, tzinfo=timezone.utc)
CUTOFF = LAST + timedelta(hours=1)


def settlement(event, date):
    ticker = date.strftime("SOL%y%m%d%H%M")
    source = event["source"]
    expiry = datetime.fromisoformat(event["expiryDate"])
    if (event["ticker"] != ticker or event["series"] != "SOL1H" or event["category"] != "Crypto"
            or event["status"] != "settled" or expiry != date or event["sourceDetails"]["index"] != source
            or source not in ("LS-LRRR-SOLNUSD", "GRR-KAIKO_RFR_SOLUSD_60S")
            or not expiry <= datetime.fromisoformat(event["resolvedAt"]) <= CUTOFF or not event["contracts"]):
        raise ValueError("Unexpected Gemini SOL event, index or resolution time")
    value = Fraction(event["settlement"]["value"])
    if value <= 0:
        raise ValueError("The Gemini index close must be positive")
    for c in event["contracts"]:
        strike = c["strike"]
        outcome = "yes" if value >= Fraction(strike["value"]) else "no"
        if (c["source"] != source or c["status"] != "settled" or c["marketState"] != "closed"
                or c["instrumentSymbol"] != f"GEMI-{ticker}-{c['ticker']}" or c["ticker"] != "HI" + strike["value"]
                or c["expiryDate"] != event["expiryDate"] or strike["type"] != "above" or c["resolutionSide"] != outcome
                or Fraction(c["settlementValue"]) != value or not expiry <= datetime.fromisoformat(c["resolvedAt"]) <= CUTOFF):
            raise ValueError("The SOL contract outcomes do not reproduce the index close")
    return source, value


def windows(events):
    included, excluded = [], []
    for week in range(13):
        end = LAST - timedelta(weeks=week)
        start = end - timedelta(days=30)
        first, last = (settlement(events[d.strftime("SOL%y%m%d%H%M")], d) for d in (start, end))
        dates = {"start": start.date().isoformat(), "end": end.date().isoformat()}
        if first[0] != last[0]:
            excluded.append(dates)
        else:
            included.append((dates, last[1] / first[1]))
    return included, excluded


def verify_history(raw):
    if hashlib.sha256(raw).hexdigest() != HISTORY_HASH:
        raise ValueError("Gemini settlement history differs from the published evidence")
    return windows(json.loads(raw))


@cache
def history():
    return verify_history(gzip.decompress(HISTORY.read_bytes()))


@cache
def payoff_fee():
    posted = json.loads(gzip.decompress(load_credit.SAMPLE.read_bytes()))
    load_credit.verify(posted)
    return posted["repay"]["meta"]["fee"]


@cache
def recorded_menu():
    raw = gzip.decompress(fuel_swap.SAMPLE.with_name("payday_observations.json.gz").read_bytes())
    if hashlib.sha256(raw).hexdigest() != "0be2a85734c03d156516dc6d6f6dc08f182216ec551cb0a484e4ec41be11b685":
        raise ValueError("The recorded funding observations changed")
    data = json.loads(raw)
    if hashlib.sha256(fuel_swap.SAMPLE.read_bytes()).hexdigest() != data["evidence_sha256"]:
        raise ValueError("Recorded observations no longer match the packaged Devnet evidence")
    return {q["customer_cents"]: q for q in data["observations"]}


def quotes(menu, live=False):
    if not live:
        return [recorded_menu()[net] for net in customer_credit.MENUS[menu]]
    read = devnet.read_with_retry()
    reserve = reserve_credit.reserve_leg(read)
    return [fuel_swap.price(reserve, net, read) for net in customer_credit.MENUS[menu]]


def inputs(menu, fee, funds, extra, floor, stress):
    customer_credit.inputs(menu, fee, funds, extra)
    integer(floor, "cash to keep on payday", 100000000)
    if stress is not None:
        integer(stress, "additional all-in SOL price", 10000000)


def price_cases(reference, stress):
    observed, excluded = history()
    cases = [{"label": "Unchanged SOL price", "price_cents": reference}]
    for dates, ratio in observed:
        price = reference * ratio
        cases.append({**dates, "label": dates["start"] + " to " + dates["end"],
                      "price_cents": (2 * price.numerator + price.denominator) // (2 * price.denominator)})
    if stress is not None:
        cases.append({"label": "Driver’s extra price", "price_cents": stress})
    return cases, excluded


def available_plans(observations, fee, extra, keep_profile):
    plans = []
    for q in observations:
        loan, net, scale = q["loan"], q["customer_cents"], q["scale"]
        customer_fee = customer_credit.customer_fee(net, fee)
        invoice = reserve_credit.INVOICE_CENTS - net - customer_fee
        base = {"customer_cents": net, "customer_fee_cents": customer_fee, "invoice_cents": invoice}
        for r in q["routes"]:
            reserve = r["reserve_raw"] // scale
            cash = r["wallet_raw"] // scale
            plans.append({**base, "kind": "sale", "pool": r["pool"], "entry_lamports": r["entry_lamports"],
                          "cash_used_cents": cash, "reserve_cents": reserve, "venue_cents": r["swap_raw"] // scale,
                          "post_refill_cents": invoice - reserve - cash, "restore_lamports": r["spent_lamports"] + r["fee_lamports"],
                          "sold_lamports": r["spent_lamports"], "slot": r["slot"], "reserve_left_cents": r["retained_raw"] // scale})
        reserve = loan["reserve_raw"] // scale
        repayment = loan["payday_stress"]["repay_cents"]
        plans.append({**base, "kind": "loan", "pool": None, "entry_lamports": customer_credit.entry_budget(loan),
                      "cash_used_cents": 0, "reserve_cents": reserve, "venue_cents": loan["borrow_raw"] // scale,
                      "minimum_price": int((Decimal(loan["sol_price"]) * (100 - loan["drop"])).to_integral_value(rounding=ROUND_CEILING)),
                      "repay_cents": repayment, "post_refill_cents": invoice - reserve - repayment - extra,
                      "restore_lamports": (0 if keep_profile else loan["profile_rent_lamports"]) + loan["fee_lamports"] + payoff_fee(),
                      "sold_lamports": 0, "profile_lamports": loan["profile_rent_lamports"],
                      "slot": q["entry_slot"], "reserve_left_cents": loan["retained_raw"] // scale})
    return plans


def floor_sum(count, modulus, slope, offset):
    total = 0
    while count:
        whole, slope = divmod(slope, modulus)
        total += whole * count * (count - 1) // 2
        whole, offset = divmod(offset, modulus)
        total += whole * count
        count, offset = divmod(slope * count + offset, modulus)
        modulus, slope = slope, modulus
    return total


def worst_rounding_gap(left, right, denominator, low, high):
    delta = left - right
    edge = high if delta > 0 else low
    if not delta:
        return 0, edge
    upper = -(-delta * edge // denominator)
    if delta * edge % denominator == 0:
        return upper, edge
    # Only this price band can round up to the upper bound. Floor sums count the hits.
    boundary = (upper - 1) * denominator
    start = max(low, boundary // delta + 1) if delta > 0 else low
    stop = min(high, (-boundary - 1) // -delta) if delta < 0 else high

    def hits(end):
        n = end - start + 1
        def rounded_sum(slope):
            return floor_sum(n, denominator, slope, slope * start + denominator - 1)
        return rounded_sum(left) - rounded_sum(right) - n * (upper - 1)

    if hits(stop) == 0:
        return upper - 1, edge
    lo, hi = start, stop
    while lo < hi:
        mid = (lo + hi) // 2
        if hits(mid):
            hi = mid
        else:
            lo = mid + 1
    return upper, lo


def rank(plans, prices, scale, funds, floor):
    denominator = 100 * scale * 10**9
    for p in plans:
        p["cash"] = [p["post_refill_cents"] - (p["restore_lamports"] * price * 10**6 + denominator - 1) // denominator
                     for price in prices]
        p["least_cents"], p["most_cents"] = min(p["cash"]), max(p["cash"])
        p["outside_stress"] = min(prices) < p.get("minimum_price", 0)
        p["entry_gap"] = max(0, p["entry_lamports"] - funds)
        p["payday_gap"] = max(0, floor - p["least_cents"])
    affordable = [p for p in plans if not p["entry_gap"]]
    if not affordable:
        return None
    eligible = [p for p in affordable if not p["outside_stress"] and not p["payday_gap"]]
    if not eligible:
        return None
    low, high = min(prices), max(prices)
    for p in eligible:
        regret, witness = 0, low
        for rival in affordable:
            start = max(low, rival.get("minimum_price", 0))
            if start > high:
                continue
            gap, price = worst_rounding_gap(p["restore_lamports"] * 10**6, rival["restore_lamports"] * 10**6,
                                            denominator, start, high)
            gap += rival["post_refill_cents"] - p["post_refill_cents"]
            if gap > regret:
                regret, witness = gap, price
        p["regret_cents"], p["regret_price"] = regret, witness
    return min(eligible, key=lambda p: (p["regret_cents"], -p["least_cents"], p["customer_cents"], p["entry_lamports"]))


def decide(observations, menu, fee, funds, extra, floor=0, stress=None, *, budget_cents=None, keep_profile=False):
    inputs(menu, fee, funds, extra, floor, stress)
    if type(keep_profile) is not bool:
        raise ValueError("Choose whether to leave setup money tied up")
    first = observations[0]
    if tuple(q["customer_cents"] for q in observations) != customer_credit.MENUS[menu]:
        raise ValueError("The funding menu and observed customer offers differ")
    wallet = {k: first["wallet"][k] for k in ("address", "lamports", "usdc_raw")}
    slots = [q["wallet"]["slot"] for q in observations] + [q["entry_slot"] for q in observations]
    if (max(slots) - min(slots) > 240 or any(q["loan"]["payday_stress"]["days"] != 30 or q["holdback"] != first["holdback"] or q["scale"] != first["scale"]
            or any(q["wallet"][k] != v for k, v in wallet.items()) for q in observations)):
        raise ValueError("Wallet, next-stop budget or observation window changed between offers")
    reference = int((Decimal(first["loan"]["sol_price"]) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    cases, excluded = price_cases(reference, stress)
    unit = Fraction(reference * 10**6, 100 * first["scale"] * 10**9)
    if budget_cents is not None:
        integer(budget_cents, "spendable wallet budget", 100000000)
        funds = int(budget_cents / unit)
    cap = min(funds, wallet["lamports"])
    plans = available_plans(observations, fee, extra, keep_profile)
    for p in plans:
        p["entry_cents"] = -(-p["entry_lamports"] * unit.numerator // unit.denominator)
        if p["customer_cents"] + p["cash_used_cents"] + p["venue_cents"] + p["reserve_cents"] != reserve_credit.BILL_CENTS:
            raise ValueError("Sources must exactly cover the vendor bill")
    winner = rank(plans, [c["price_cents"] for c in cases], first["scale"], cap, floor)
    wait = [p["entry_lamports"] for p in plans if not p["customer_cents"] and not p["payday_gap"] and not p["outside_stress"]]
    eligible = [p for p in plans if not p["payday_gap"] and not p["entry_gap"] and not p["outside_stress"]]
    return {"winner": winner, "plans": plans, "minimum_early_cents": min((p["customer_cents"] for p in eligible), default=None),
            "wait_entry": min(wait, default=None), "available_lamports": cap, "actual_lamports": wallet["lamports"],
            "invoice_cents": reserve_credit.INVOICE_CENTS, "floor_cents": floor, "keep_profile": keep_profile, "available_cents": int(cap * unit),
            "stress_cents": stress, "recorded_backup": cap == stop_backup.DEFAULT_CAP, "budget_cents": budget_cents, "reference_cents": reference, "cases": cases, "excluded": excluded,
            "first_slot": min(slots), "last_slot": max(slots), "bill_cents": reserve_credit.BILL_CENTS}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reprice recorded funding routes against 30-day Gemini SOL moves")
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--budget-cents", type=int)
    budget.add_argument("--wallet-lamports", type=int)
    parser.add_argument("--floor-cents", type=int, default=0, help="Minimum cash on payday")
    parser.add_argument("--stress-cents", type=int)
    parser.add_argument("--evidence", type=Path, help="Downloaded Devnet exchange checks")
    parser.add_argument("--keep-profile", action="store_true")
    parser.add_argument("--gemini-evidence", type=Path, help="Verify the downloaded Gemini settlement history")
    args = parser.parse_args()
    if args.gemini_evidence:
        verify_history(args.gemini_evidence.read_bytes())
    if args.evidence:
        data = json.loads(args.evidence.read_bytes())
        checked = [fuel_swap.replay(data[str(net)], net) for net in customer_credit.ADVANCES]
    else:
        checked = [fuel_swap.saved(net) for net in customer_credit.ADVANCES]
    cents = 8000 if args.wallet_lamports is None and args.budget_cents is None else args.budget_cents
    print(json.dumps(decide(checked, "both", 100, args.wallet_lamports or 0, 0, args.floor_cents, args.stress_cents,
                            budget_cents=cents, keep_profile=args.keep_profile), indent=2))
