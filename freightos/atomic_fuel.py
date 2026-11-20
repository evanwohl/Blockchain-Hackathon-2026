"""Fund one vendor transfer with a Kamino vault redemption and a collateralized draw."""
import argparse
import base64
import gzip
import json
from decimal import Decimal
from functools import cache
from pathlib import Path

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV1, TransactionConfig
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from freightos import devnet, freight_credit as credit, funding, next_stop, public_offer, reserve_credit
from freightos.reserve import payment_instructions
from hedging.quick_pay import integer

SAMPLE = credit.DATA / "atomic_fuel.json.gz"
LATE_SAMPLE = credit.DATA / "atomic_late.json.gz"
LOAD = "FOS-RESERVE-LOAN-190"


def message(reserve, collateral, rent, block, *, reject=False, check_remainder=False):
    plan = next_stop.payment()
    total = reserve["fuel_cents"] * reserve["raw_units_per_cent"]
    advance = total - reserve["vendor_raw"]
    units = reserve["eligible_shares_raw"] - reserve["retained_shares_raw"]
    change = reserve["wallet_increase_raw"]
    stage, (_, create, init, redeem, pay) = payment_instructions(
        plan.route, plan.payer, plan.destination, LOAD, total + (change + 1 if reject else 0),
        units, rent, venue=devnet.VENUE)
    ops = [create, init, redeem]
    for ix in credit.loan_instructions(collateral, advance, "wallet"):
        accounts = [AccountMeta(stage if a.pubkey == plan.cash else a.pubkey, a.is_signer, a.is_writable) for a in ix.accounts]
        ops.append(Instruction(ix.program_id, ix.data, accounts))
    payment_index = len(ops)
    ops.append(pay)
    if change:
        ops.append(public_offer.transfer(stage, plan.payer, plan.cash, change))
    ops.append(Instruction(devnet.TOKEN, b"\x09", [AccountMeta(stage, False, True),
                           AccountMeta(plan.payer, False, True), AccountMeta(plan.payer, True, False)]))
    if check_remainder:
        ops.append(devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares,
                                           reserve["retained_shares_raw"], venue=devnet.VENUE))
    config = TransactionConfig(compute_unit_limit=700000, loaded_accounts_data_size_limit=16 * 1024 * 1024, heap_size=32768)
    msg = MessageV1.try_compile(plan.payer, ops, Hash.from_string(block), config)
    return msg, stage, payment_index


def simulate(reserve, collateral, rent, block, slot, rpc, *, reject=False, check_remainder=False):
    msg, stage, index = message(reserve, collateral, rent, block, reject=reject, check_remainder=check_remainder)
    wire = bytes(VersionedTransaction.populate(msg, [Signature.default()]))
    if len(wire) > 4096:
        raise ValueError("The payment exceeds the v1 Solana transaction limit")
    accounts = [credit.route()[key] for key in ("obligation", "usd", "sol")]
    result = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {
        "encoding": "base64", "commitment": "confirmed", "sigVerify": False, "minContextSlot": slot,
        "accounts": {"encoding": "base64", "addresses": accounts}}])
    if result["context"]["slot"] < slot:
        raise ValueError("The simulation predates the reserve quote")
    return msg, stage, index, result, len(wire)


def price(reserve, collateral, rent, block, slot, drop, days, rpc):
    plan = next_stop.payment()
    msg, stage, _, result, size = simulate(reserve, collateral, rent, block, slot, rpc)
    value = result["value"]
    if value["err"] is not None:
        raise ValueError("Kamino could not fund the complete vendor payment")
    keys = list(msg.account_keys)
    units = reserve["eligible_shares_raw"] - reserve["retained_shares_raw"]
    before, after = devnet.token_balances(value, keys, plan.shares, devnet.SHARES, plan.payer)
    vault_before, vault_after = devnet.token_balances(value, keys, plan.custody, devnet.MINT, plan.vault_authority)
    i = keys.index(stage)
    if (before < reserve["eligible_shares_raw"] or before - after != units
            or after < reserve["retained_shares_raw"] or value["preBalances"][i] or value["postBalances"][i]
            or vault_before - vault_after != reserve["vendor_raw"] + reserve["wallet_increase_raw"]):
        raise ValueError("Vault shares, isolated payment funds or the next-stop reserve do not reconcile")
    def project(horizon, rate_multiplier):
        return credit.evaluate_deltas({"collateral": collateral, "response": result, "checked_at": ""}, drop, horizon,
                                     reserve["shortfall_raw"], reserve_credit.PLEDGE_STEP, "vendor", keys, plan.payer,
                                     Pubkey.from_string(credit.route()["obligation"]), True, reserve_paid=reserve["vendor_raw"],
                                     change_returned=reserve["wallet_increase_raw"], rate_multiplier=rate_multiplier)
    q = project(days, 1)
    q["late"] = project(days + 7, 2)

    obligation = keys.index(Pubkey.from_string(credit.route()["obligation"]))
    metadata = keys.index(Pubkey.from_string(credit.route()["ixs"][4]["a"][2][0]))
    q["obligation_rent"] = value["postBalances"][obligation] - value["preBalances"][obligation]
    q["profile_rent"] = value["postBalances"][metadata] - value["preBalances"][metadata]
    if min(q["obligation_rent"], q["profile_rent"]) <= 0 or q["obligation_rent"] + q["profile_rent"] != q["setup_lamports"]:
        raise ValueError("The loan account deposits do not match the carrier's setup funds")

    return q, size


def terms(reserve, drop, days, rpc, *, prepare=False, protect=False):
    credit.terms(drop, days)
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("Use public Solana Devnet for this payment")
    slot = reserve["slot"]
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    validity = rpc("getLatestBlockhash", [{"commitment": "confirmed", "minContextSlot": slot}])["value"]
    block = validity["blockhash"]
    pledge = credit.SEED_COLLATERAL
    for _ in range(4):
        q, size = price(reserve, pledge, rent, block, slot, drop, days, rpc)
        slot = q["slot"]
        required = max(q["minimum_lamports"], q["late"]["minimum_lamports"]) if protect else q["minimum_lamports"]
        if required == pledge:
            break
        pledge = required
    else:
        raise ValueError("Collateral prices moved while sizing the combined payment")
    msg, _, vendor_index, refused, _ = simulate(reserve, pledge, rent, block, slot, rpc, reject=True)
    failed = refused["value"]
    expected = list(failed["preBalances"])
    expected[list(msg.account_keys).index(credit.OWNER)] -= failed["fee"]
    if (failed["err"] != {"InstructionError": [vendor_index, {"Custom": 1}]}
            or failed["fee"] != q["fee_lamports"] or failed["postBalances"] != expected
            or failed["preTokenBalances"] != failed["postTokenBalances"]):
        raise ValueError("The underfunded payment did not roll back its loans and transfers")
    msg, _, _, follow, _ = simulate(reserve, pledge, rent, block, refused["context"]["slot"], rpc, check_remainder=True)
    plan = next_stop.payment()
    if follow["value"]["err"] is not None:
        raise ValueError("The protected fuel reserve could not be redeemed after the payment")
    balance = next_stop.balances(plan, list(msg.account_keys), follow["value"])
    retained = balance["cash"][1] - reserve["wallet_increase_raw"]
    if (retained < reserve["holdback"]["cost_cents"] * reserve["raw_units_per_cent"]
            or balance["shares"][1] != -reserve["eligible_shares_raw"]
            or balance["custody"][1] != -retained - reserve["wallet_increase_raw"] - reserve["vendor_raw"]
            or balance["vendor"][1] != reserve["vendor_raw"] + reserve["shortfall_raw"]):
        raise ValueError("The remaining shares do not cover the next stop")
    total = reserve["fuel_cents"] * reserve["raw_units_per_cent"]
    report = json.loads((funding.DATA / "case_report.json").read_bytes())
    redeemed_cents = Decimal(reserve["vendor_raw"] + reserve["wallet_increase_raw"]) / reserve["raw_units_per_cent"]
    def growth(horizon):
        apy = Decimal(funding.refill_cost(report, horizon)["apy"])
        return (1 + apy) ** (Decimal(horizon) / 365) - 1
    ordinary_growth, late_growth = growth(days), growth(days + 7)
    missed = credit.ceiling(redeemed_cents * ordinary_growth)
    late = q["late"]
    delayed_cost = late["cost_cents"] + credit.ceiling(redeemed_cents * late_growth)
    early_pay_fee = reserve_credit.INVOICE_CENTS - reserve["fuel_cents"]
    prepared = {}
    if prepare:
        if rpc("getBlockHeight", [{"commitment": "confirmed"}]) + 10 >= validity["lastValidBlockHeight"]:
            raise ValueError("The transaction is near expiry. Request a fresh payment.")
        msg, _, _ = message(reserve, pledge, rent, block)
        prepared = {"transaction": base64.b64encode(bytes(VersionedTransaction.populate(msg, [Signature.default()]))).decode(),
                    "last_valid_height": validity["lastValidBlockHeight"], "borrower": str(credit.OWNER),
                    "vendor": str(next_stop.payment().destination)}
    stress = {"protected": protect, "days": days + 7, "repay_cents": late["repay_cents"],
              "cost_cents": delayed_cost, "rate_bps": late["rate_bps"], "original_rate_bps": q["rate_bps"],
              "ordinary_pledge_lamports": q["minimum_lamports"], "required_lamports": late["minimum_lamports"],
              "stress_ltv_bps": late["stress_ltv_bps"], "target_ltv_bps": (late["liquidation_pct"] - 10) * 100}
    return {**prepared, "annual_rate": q["annual_rate"], "initial_debt_raw": q["initial_debt_raw"], "sol_price": q["sol_price"],
            "payday_stress": stress, "reserve_growth": str(late_growth if protect else ordinary_growth), "bill_cents": reserve["fuel_cents"], "scale": reserve["raw_units_per_cent"], "vendor_raw": total,
            "reserve_raw": reserve["vendor_raw"], "borrow_raw": reserve["shortfall_raw"], "retained_raw": retained,
            "holdback": reserve["holdback"], "repay_cents": q["repay_cents"], "pledge_lamports": pledge,
            "setup_lamports": q["setup_lamports"], "obligation_rent_lamports": q["obligation_rent"],
            "profile_rent_lamports": q["profile_rent"], "fee_lamports": q["fee_lamports"], "slot": slot,
            "interest_cents": q["cost_cents"], "missed_yield_cents": missed, "early_pay_fee_cents": early_pay_fee,
            "cost_room_cents": early_pay_fee - q["cost_cents"] - missed, "remainder_slot": follow["context"]["slot"],
            "refused_slot": refused["context"]["slot"], "wire_bytes": size, "days": days, "drop": drop}


def partial_invoice(invoice, bill, cap, fee, growth, reserve_fee, loan_limit):
    best = None
    fee_limit = None
    for draw in range(min(cap, bill - 1) + 1):
        net = bill - draw
        expense = reserve_fee if draw else 0
        missed = credit.ceiling(draw * growth)
        max_gross = min(invoice - 1, invoice - draw - expense)
        if loan_limit is not None:
            max_gross = min(max_gross, net + loan_limit - missed - expense)
        if max_gross >= net:
            ceiling = invoice * (max_gross - net) // max_gross
            fee_limit = ceiling if fee_limit is None else max(fee_limit, ceiling)
        if fee == invoice:
            continue
        gross = (net * invoice + invoice - fee - 1) // (invoice - fee)
        if gross >= invoice:
            continue
        discount = gross - net
        left = invoice - gross - draw - expense
        if left < 0:
            continue
        cost = discount + missed + expense
        if best is None or cost < best["cost_cents"]:
            best = {"gross_cents": gross, "advance_cents": net, "discount_cents": discount, "draw_cents": draw,
                    "regular_cents": invoice - gross, "payday_cash_cents": left, "missed_yield_cents": missed,
                    "expense_cents": expense, "cost_cents": cost}
    return best, fee_limit


def compare_offer(quote, fee_cents, other_cents, *, partial=False, reserve_fee_cents=0):
    invoice = reserve_credit.INVOICE_CENTS
    fee = integer(fee_cents, "early payment fee", invoice)
    other = integer(other_cents, "other borrowing costs", 100_000_000)
    ordinary = quote["interest_cents"] + quote["missed_yield_cents"]
    late = quote["payday_stress"]
    loan_cost = other + (max(ordinary, late["cost_cents"]) if late["protected"] else ordinary)
    net = invoice - fee
    payoff = max(quote["repay_cents"], late["repay_cents"]) if late["protected"] else quote["repay_cents"]
    refill = credit.ceiling(Decimal(quote["reserve_raw"]) / quote["scale"])
    payday = invoice - payoff - refill - other
    fuel_room = invoice - quote["bill_cents"]
    ceiling = min(loan_cost, fuel_room) if payday >= 0 else fuel_room
    choice = "early" if fee <= ceiling else "loan" if payday >= 0 else "short"
    if type(partial) is not bool:
        raise ValueError("Choose whether the customer allows a proportional advance")
    reserve_fee = integer(reserve_fee_cents, "reserve payment expenses", 100_000_000)
    split, fee_limit = None, None
    if partial:
        split, fee_limit = partial_invoice(invoice, quote["bill_cents"], quote["reserve_raw"] // quote["scale"],
                                fee, Decimal(quote["reserve_growth"]), reserve_fee, loan_cost if payday >= 0 else None)
        if split is not None and (choice == "short" or (choice == "loan" and split["cost_cents"] <= loan_cost)
                                  or (choice == "early" and split["cost_cents"] < fee)):
            choice = "partial"
    return {"choice": choice, "partial": split, "partial_fee_limit_cents": fee_limit, "partial_allowed": partial, "reserve_fee_cents": reserve_fee, "payday_cash_cents": payday, "fee_cents": fee, "other_cents": other,
            "early_net_cents": net, "shortfall_cents": max(0, quote["bill_cents"] - net),
            "loan_cost_cents": loan_cost, "early_fee_ceiling_cents": ceiling}


def opening_amounts(msg, meta, owner):
    plan = next_stop.payment()
    if owner != plan.payer or msg.account_keys[0] != owner:
        raise ValueError("The combined payment belongs to a different carrier")
    keys = list(msg.account_keys)
    bills = reserve_credit.BILL_CENTS
    scale = 100
    ix_amounts = {bytes(ix.data[:8]): int.from_bytes(ix.data[8:], "little") for ix in msg.instructions
                  if keys[ix.program_id_index] == credit.KLEND and len(ix.data) == 16}
    pledge = ix_amounts.get(bytes.fromhex("d8e0bf1bcc9766af"), 0)
    debt = ix_amounts.get(bytes.fromhex("a1808ff5abc7c206"), 0)
    if not pledge or not 0 < debt < bills * scale:
        raise ValueError("The combined transaction has no matching fuel loan")
    shares = devnet.token_balances(meta, keys, plan.shares, devnet.SHARES, owner)
    paid = devnet.token_balances(meta, keys, plan.destination, devnet.MINT, plan.payee)
    vault = devnet.token_balances(meta, keys, plan.custody, devnet.MINT, plan.vault_authority)
    reserve_paid = bills * scale - debt
    returned = 0
    if plan.cash in keys:
        cash = devnet.token_balances(meta, keys, plan.cash, devnet.MINT, owner)
        returned = cash[1] - cash[0]
    if (paid[1] - paid[0] != bills * scale or shares[0] <= shares[1] or returned < 0
            or vault[0] - vault[1] != reserve_paid + returned):
        raise ValueError("The combined loan and vendor proceeds do not reconcile")
    reserve = {"fuel_cents": bills, "raw_units_per_cent": scale, "vendor_raw": reserve_paid,
               "eligible_shares_raw": shares[0] - shares[1], "retained_shares_raw": 0, "wallet_increase_raw": returned}
    rent = int.from_bytes(msg.instructions[0].data[-48:-40], "little")
    expected, _, _ = message(reserve, pledge, rent, str(msg.recent_blockhash))
    if expected != msg:
        raise ValueError("The signed instructions differ from the combined payment")
    return pledge, debt


def record(calls, rpc):
    def observe(method, params):
        response = rpc(method, params)
        calls.append({"method": method, "params": params, "response": response})
        return response
    return observe


def replay(reserve, drop, days, calls, *, protect=False):
    rows = iter(calls)
    def read(method, params):
        row = next(rows, None)
        if row is None or method != row["method"] or params != row["params"]:
            raise ValueError("The recorded transaction differs from the payment")
        return row["response"]
    result = terms(reserve, drop, days, read, protect=protect)
    if next(rows, None) is not None:
        raise ValueError("Unexpected calls after the combined payment")
    return result


@cache
def snapshot(protect=False):
    return json.loads(gzip.decompress((LATE_SAMPLE if protect else SAMPLE).read_bytes()))


@cache
def saved_reserve(protect=False):
    return reserve_credit.replay_reserve(snapshot(protect)["reserve"])


@cache
def saved(drop, days, protect=False):
    credit.terms(drop, days)
    return replay(saved_reserve(protect), drop, days, snapshot(protect)["cases"][f"{drop}-{days}"], protect=protect)


def live(drop, days, rpc=devnet.request, *, prepare=False, protect=False):
    credit.terms(drop, days)
    return terms(reserve_credit.reserve_leg(rpc), drop, days, rpc, prepare=prepare, protect=protect)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify a combined Kamino fuel payment simulation")
    parser.add_argument("evidence", type=Path, nargs="?")
    parser.add_argument("--protect-payday", action="store_true", help="Size the pledge for seven extra days at twice the quoted rate")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--unsigned-out", type=Path, help="Save a fresh Devnet transaction for the demo carrier")
    parser.add_argument("--drop", type=int, default=40, choices=(20, 40, 60))
    parser.add_argument("--days", type=int, default=23, choices=(23, 30))
    args = parser.parse_args()
    if args.live:
        result = live(args.drop, args.days, prepare=bool(args.unsigned_out), protect=args.protect_payday)
        if args.unsigned_out:
            args.unsigned_out.write_bytes(base64.b64decode(result.pop("transaction")))
    else:
        if args.unsigned_out:
            parser.error("--unsigned-out requires --live")
        sample = json.loads(args.evidence.read_bytes()) if args.evidence else snapshot(args.protect_payday)
        result = replay(reserve_credit.replay_reserve(sample["reserve"]), args.drop, args.days,
                        sample["cases"][f"{args.drop}-{args.days}"], protect=args.protect_payday or sample.get("protected_payday") is True)
    print(json.dumps(result, indent=2))
