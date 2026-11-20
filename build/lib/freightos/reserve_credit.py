"""Compare two verified Kamino funding routes for the same upcoming fuel bill."""
import gzip
import json
from decimal import Decimal
from functools import cache

from freightos import devnet, freight_credit as credit, fuel_update, funding, next_stop, public_offer

SNAPSHOT = credit.DATA / "reserve_credit.json.gz"
BILL_CENTS = 19000
INVOICE_CENTS = 20000
PLEDGE_STEP = 10000


@cache
def prior_payments():
    return (devnet.verify(json.loads(devnet.SAMPLE.read_bytes())),
            public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes())))


def reserve_leg(rpc):
    first, second = prior_payments()
    keep = next_stop.fuel_holdback(3, fuel_update.saved(), oil_rise=True)
    reserve = next_stop.preview(first, second, BILL_CENTS, 0, rpc, holdback=keep)
    plan = next_stop.payment()
    if plan.payer != credit.OWNER or str(plan.destination) != first["decision"]["vendor"]:
        raise ValueError("The funding routes must pay the same vendor from the same carrier")
    scale = reserve["raw_units_per_cent"]
    paid, cents_left = divmod(reserve["vendor_raw"], scale)
    if (cents_left or not 0 < paid < BILL_CENTS or not reserve.get("retained_raw")
            or reserve["retained_raw"] < keep["cost_cents"] * scale):
        raise ValueError("The remaining reserve cannot fund both parts of this example")
    return reserve


def capture_loans(reserve, drop, days, rpc):
    scale = reserve["raw_units_per_cent"]
    runs = []
    slot = reserve["slot"]
    for raw in (BILL_CENTS * scale, BILL_CENTS * scale - reserve["vendor_raw"]):
        run = credit.capture(drop, days, rpc, advance=raw, min_slot=slot, step=PLEDGE_STEP)
        runs.append(run)
        slot = run["response"]["context"]["slot"]
    return runs


def compare(reserve, runs, drop, days):
    credit.terms(drop, days)
    scale = reserve["raw_units_per_cent"]
    total = BILL_CENTS * scale
    paid = reserve["vendor_raw"]
    holdback = next_stop.fuel_holdback(3, fuel_update.saved(), oil_rise=True)
    if (scale != 100 or reserve["fuel_cents"] != BILL_CENTS or reserve["holdback"] != holdback
            or not 0 < paid < total or paid % scale or reserve["wallet_increase_raw"] < 0
            or reserve["retained_raw"] < holdback["cost_cents"] * scale
            or reserve["shortfall_raw"] != total - paid):
        raise ValueError("Reserve funding and protected gallons do not match the bill")
    baseline, split = [credit.evaluate(run, drop, days, raw, step=PLEDGE_STEP)
                       for run, raw in zip(runs, (total, total - paid), strict=True)]
    if (not reserve["slot"] <= baseline["slot"] <= split["slot"]
            or any(run.get("wire") is None for run in runs)
            or any(q["minimum_lamports"] != q["collateral_lamports"] for q in (baseline, split))
            or baseline["setup_lamports"] != split["setup_lamports"]):
        raise ValueError("Loan checks differ from the collateral or setup assumptions")
    price = Decimal(baseline["sol_price"])
    yield_rate = funding.refill_cost(json.loads((funding.DATA / "case_report.json").read_bytes()), days)
    redeemed = Decimal(paid + reserve["wallet_increase_raw"]) / scale
    carry = credit.ceiling(redeemed * ((1 + Decimal(yield_rate["apy"])) ** (Decimal(days) / 365) - 1))
    base_fee = credit.ceiling(100 * price * baseline["fee_lamports"] / credit.LAMPORTS)
    split_fee = credit.ceiling(100 * price * (split["fee_lamports"] + reserve["network_fee_lamports"]) / credit.LAMPORTS)
    base_cost = baseline["cost_cents"] + base_fee
    before_yield = split["cost_cents"] + split_fee
    room = base_cost - before_yield - 1
    ceiling_bps = None if room < 0 else int(10000 * ((1 + Decimal(room) / redeemed) ** (Decimal(365) / days) - 1))
    common_repay = Decimal(split["initial_debt_raw"]) * (Decimal(baseline["annual_rate"]) * days / 365).exp()
    common_cost = credit.ceiling((common_repay - split["advance_raw"]) / scale) + split_fee + carry
    return {"same_rate_saved_cents": base_cost - common_cost,
            "full_rate_bps": baseline["rate_bps"], "split_rate_bps": split["rate_bps"],
            "invoice_cents": INVOICE_CENTS, "bill_cents": BILL_CENTS, "reserve_cents": paid // scale, "borrow_cents": split["advance_cents"],
            "keep_cents": reserve["retained_raw"] // scale, "holdback": reserve["holdback"],
            "projected_repay_cents": split["repay_cents"], "full_repay_cents": baseline["repay_cents"],
            "baseline_cost_cents": base_cost, "split_cost_cents": before_yield + carry,
            "saved_cents": base_cost - before_yield - carry, "missed_yield_cents": carry,
            "base_fee_cents": base_fee, "split_fee_cents": split_fee,
            "max_yield_bps": ceiling_bps, "yield": yield_rate, "days": days, "drop": drop,
            "entry_cash_cents": split["entry_cash_cents"],
            "full_pledge_millis": baseline["collateral_lamports"] // PLEDGE_STEP,
            "split_pledge_millis": split["collateral_lamports"] // PLEDGE_STEP,
            "reserve_slot": reserve["slot"], "loan_slot": split["slot"], "checked_at": split["checked_at"]}


def replay_reserve(records):
    calls = iter(records)
    def replay(method, params):
        row = next(calls, None)
        if row is None or (method, params) != (row["method"], row["params"]):
            raise ValueError("Reserve payment differs from the saved instructions")
        return row["response"]
    reserve = reserve_leg(replay)
    if next(calls, None) is not None:
        raise ValueError("Unexpected reserve evidence")
    return reserve


@cache
def snapshot():
    return json.loads(gzip.decompress(SNAPSHOT.read_bytes()))


def saved(drop, days):
    credit.terms(drop, days)
    return compare(snapshot()["reserve_result"], snapshot()["loans"][f"{drop}-{days}"], drop, days)


def live(drop, days):
    reserve = reserve_leg(devnet.request)
    return compare(reserve, capture_loans(reserve, drop, days, devnet.request), drop, days)


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Replay the reserve and loan simulations for the $190 sample bill")
    parser.add_argument("evidence", nargs="?", type=Path, help="Downloaded JSON evidence")
    args = parser.parse_args()
    data = json.loads(args.evidence.read_bytes()) if args.evidence else snapshot()
    reserve = replay_reserve(data["reserve"])
    if reserve != data["reserve_result"]:
        raise ValueError("Recorded reserve quote differs from its simulations")
    for terms, loans in data["loans"].items():
        drop, days = map(int, terms.split("-"))
        print(json.dumps(compare(reserve, loans, drop, days)))
