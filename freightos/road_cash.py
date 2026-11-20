"""Price feasible changes to the driver's separate cash and SOL budget."""
import argparse
import json
from functools import cache
from pathlib import Path
from typing import NamedTuple

from freightos import atomic_fuel, devnet, freight_credit, fuel_floor, mainnet_stop, payday_policy, reserve_credit
from hedging.quick_pay import integer


class Payment(NamedTuple):
    separate: int
    wallet_cash: int
    reserve: int
    exchange: int
    restore: int
    entry: int
    pool: str | None


def payments(quote, native):
    market = {**fuel_floor.verified_curves(quote), "cap_lamports": native}
    rows = []
    for separate in range(market["bill_cents"] + 1):
        p = fuel_floor.price_payment(market, separate, 0, 0)
        if p is not None:
            rent = market["rent_lamports"] if p["exchange_cents"] else 0
            rows.append(Payment(separate, p["wallet_cash_cents"], p["reserve_cents"], p["exchange_cents"],
                                p["restore_lamports"], p["restore_lamports"] + rent, p["pool"]))
    return rows


def recorded_wallet(data):
    matching = [r for r in data["calls"] if r["method"] == "getMultipleAccounts" and
                r["params"][0] == [str(freight_credit.OWNER), str(devnet.associated(freight_credit.OWNER, devnet.MINT))]]
    wallet = atomic_fuel.replay_calls(matching, lambda read: devnet.wallet_balances(str(freight_credit.OWNER), read))
    q = data["result"]
    if (not q["first_slot"] <= wallet["slot"] <= q["last_slot"] or
            int(wallet["usdc_raw"]) // q["scale"] != q["market"]["wallet_cents"]):
        raise ValueError("The wallet balance differs from the recorded pool comparison")
    return wallet["lamports"]


@cache
def recorded():
    data = fuel_floor.saved_evidence()
    quote, native = data["result"], recorded_wallet(data)
    return quote, native, payments(quote, native)


def limits(budget, cash, received, target, stress, preference="keep-cash", offer_menu="partial", offer_fee=100, offer_amount=None):
    mainnet_stop.validate_offer(offer_menu, offer_fee, offer_amount)
    if preference not in ("keep-cash", "price-room"):
        raise ValueError("Choose how to use the available cash")
    integer(budget, "wallet budget", 100000000)
    integer(cash, "cash for fuel", 100000000)
    integer(received, "freight received", reserve_credit.INVOICE_CENTS)
    integer(target, "payday cash target", 100000000)
    if stress is not None:
        integer(stress, "all-in SOL price", 10000000)


def model(quote, native, rows, budget, cash, received, target, reference, prices, preference="keep-cash"):
    bill, scale = quote["bill_cents"], quote["scale"]
    denominator = scale * 100000
    cap = min(native, budget * denominator // reference)
    high, low = max(prices), min(prices)

    def remaining(p, price):
        return received - bill + p.exchange - fuel_floor.ceil_div(p.restore * price, denominator)

    chosen = most_left = first_funding = cash_fix = wallet_fix = None
    buffered, best_limit = None, -1
    for row in rows:
        fits_wallet, fits_cash = row.entry <= cap, row.separate <= cash
        least = remaining(row, high)
        if fits_wallet:
            if first_funding is None:
                first_funding = row
            if fits_cash and (most_left is None or least > remaining(most_left, high)):
                most_left = row
            if least >= target and cash_fix is None:
                cash_fix = row
            if fits_cash:
                if chosen is None and least >= target:
                    chosen = row
                limit = fuel_floor.buyback_limit(received, bill, target, row.exchange, row.restore, scale)
                if limit > best_limit:
                    best_limit, buffered = limit, row
        if fits_cash and least >= target and (wallet_fix is None or row.entry < wallet_fix.entry):
            wallet_fix = row

    # An entirely separate cash payment always has zero wallet requirement.
    need = max(0, first_funding.separate - cash)
    pick = chosen or most_left or first_funding
    if chosen is not None and preference == "price-room":
        pick = buffered

    def tolerance(row):
        if row is None:
            return None
        limit = fuel_floor.buyback_limit(received, bill, target, row.exchange, row.restore, scale)
        state = "unbounded" if limit == float("inf") else "unreachable" if limit < 0 else "bounded"
        return {"state": state, "price_cents": limit if state == "bounded" else None,
                "cash_cents": row.separate, "wallet_cents": bill - row.separate}

    paid = pick.wallet_cash + pick.reserve + pick.exchange
    options = []
    if not chosen:
        if cash_fix is not None:
            options.append({"input": "cash", "amount_cents": cash_fix.separate, "increase_cents": cash_fix.separate-cash})
        if wallet_fix is not None:
            new_budget = fuel_floor.ceil_div(wallet_fix.entry * reference, denominator)
            options.append({"input": "budget", "amount_cents": new_budget, "increase_cents": new_budget-budget})
        if most_left is not None:
            gap = target - remaining(most_left, high)
            if 0 < gap <= reserve_credit.INVOICE_CENTS - received:
                options.append({"input": "received", "amount_cents": received + gap, "increase_cents": gap})
    known = quote["market"]["checks"].get(str(paid), []) + quote["routes"] + quote["fixed_routes"]
    checked = next((r for r in known if r["pool"] == pick.pool and r["vendor_raw"] == paid*scale and
                    r["entry_lamports"] <= cap and r["wallet_raw"] == pick.wallet_cash*scale and
                    r["reserve_raw"] == pick.reserve*scale and r["limit_lamports"] + r["fee_lamports"] == pick.restore), None)
    return {"status": "ready" if chosen else "payday_short" if most_left else "fuel_short",
            "preference": preference, "selected_limit": tolerance(pick) if not need else None,
            "cash_first": tolerance(chosen), "price_first": tolerance(buffered),
            "bill_cents": bill, "separate_cents": pick.separate, "wallet_cents": paid, "fuel_gap_cents": need,
            "untouched_cents": max(0, cash - pick.separate), "low_payday_cents": remaining(pick, high),
            "high_payday_cents": remaining(pick, low), "payday_gap_cents": max(0, target-remaining(pick, high)),
            "wallet_cash_cents": pick.wallet_cash, "reserve_cents": pick.reserve, "exchange_cents": pick.exchange,
            "protected_cents": quote["protected_cents"], "entry_lamports": pick.entry, "cap_lamports": cap,
            "repairs": options, "curve_slot": quote["market"]["curve_slot"], "payment_slot": checked["slot"] if checked else None,
            "reference_cents": reference, "wallet_capped": cap < budget * denominator // reference,
            "effective_budget_cents": cap * reference // denominator, "low_price_cents": low, "high_price_cents": high, "receipt_cents": received, "floor_cents": target}


def evaluate(budget, cash, received, target, stress, *, preference="keep-cash", live=False, evidence=None, mainnet_evidence=None, offer_menu="partial", offer_fee=100, offer_amount=None):
    limits(budget, cash, received, target, stress, preference, offer_menu, offer_fee, offer_amount)
    reference = payday_policy.reference_price(payday_policy.recorded_menu()[0])
    cases, _ = payday_policy.price_cases(reference, stress)
    prices = [c["price_cents"] for c in cases]
    if live:
        cap = budget * fuel_floor.saved()["scale"] * 100000 // reference
        q = fuel_floor.public_quotes(cap, cash, received, target, max(prices), devnet.read_with_retry(),
                                     price_room=preference == "price-room")
        if not q.get("market"):
            raise ValueError("The public pool curves could not be verified for this stop")
        native = q["market"]["wallet_lamports"]
        rows = payments(q, native)
    elif evidence is not None:
        q = fuel_floor.replay(evidence)
        native = recorded_wallet(evidence)
        rows = payments(q, native)
    else:
        q, native, rows = recorded()
    history = [case["price_cents"] for case in cases if "start" in case]
    result = model(q, native, rows, budget, cash, received, target, reference, prices, preference)
    try:
        production_prices = mainnet_stop.compare(result, q["market"], native, budget, cash, mainnet_evidence,
                                                offer_menu=offer_menu, offer_fee=offer_fee, offer_amount=offer_amount,
                                                history_prices=history)
    except (OSError, ValueError):
        if mainnet_evidence is not None:
            raise
        production_prices = None
    return {**result, "mainnet": production_prices,
            "gemini_windows": len(history), "gemini_high_cents": max(history) if history else None,
            "source": "live" if live else "recorded"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in (("budget", 8000), ("cash", 7500), ("received", 20000), ("target", 300)):
        parser.add_argument("--" + name, type=int, default=default, help="Amount in cents")
    parser.add_argument("--preference", choices=("keep-cash", "price-room"), default="keep-cash")
    parser.add_argument("--offer-menu", choices=("partial", "both", "none", "counter"), default="partial")
    parser.add_argument("--offer-fee", type=int, default=100, help="Fee in cents, flat for a counteroffer or per $99 otherwise")
    parser.add_argument("--offer-amount", type=int, help="Exact net counteroffer in cents")
    stress_group = parser.add_mutually_exclusive_group()
    stress_group.add_argument("--stress", type=int, default=15000, help="Additional SOL price in cents")
    stress_group.add_argument("--historical-only", action="store_true", help="Use only the Gemini history and unchanged price")
    parser.add_argument("--evidence", type=Path, help="Downloaded Solana pool and wallet evidence")
    parser.add_argument("--gemini-evidence", type=Path, help="Downloaded Gemini settlement history")
    parser.add_argument("--mainnet-evidence", type=Path, help="Downloaded full-size mainnet pool evidence")
    args = parser.parse_args()
    if args.gemini_evidence:
        payday_policy.verify_history(args.gemini_evidence.read_bytes())
    print(json.dumps(evaluate(args.budget, args.cash, args.received, args.target, None if args.historical_only else args.stress,
                              preference=args.preference, offer_menu=args.offer_menu, offer_fee=args.offer_fee, offer_amount=args.offer_amount,
                              mainnet_evidence=args.mainnet_evidence.read_bytes() if args.mainnet_evidence else None,
                              evidence=json.loads(args.evidence.read_bytes()) if args.evidence else None), indent=2))
