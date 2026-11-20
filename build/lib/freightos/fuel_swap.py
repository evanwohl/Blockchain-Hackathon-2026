"""Compare exact-output SOL sales for the fuel left after a customer contribution."""
import argparse
import base64
import gzip
import hashlib
import json
import struct
from decimal import Decimal
from functools import cache
from pathlib import Path

from solders.hash import Hash
from solders.message import MessageV1, TransactionConfig
from solders.signature import Signature
from solders.transaction import VersionedTransaction
from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey
from solders.system_program import CreateAccountWithSeedParams, create_account_with_seed

from freightos import atomic_fuel, customer_credit, devnet, freight_credit as credit, next_stop, public_offer, reserve_credit

ORCA = Pubkey.from_string("whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc")
POOLS = tuple(map(Pubkey.from_string, ("6XqJUqX4zUL7KEm9wGqTvJmE7DdC8e6MYeMBF9uYLckX", "75cFnbvdn4ZXf5SoY4JMrY2PvWgb1FrxqGLw8NtaHwpE")))
SAMPLE = credit.DATA / "fuel_swap.json.gz"
SEED = "FreightOS fuel exchange"


class PoolDeclined(ValueError):
    def __init__(self, slot):
        super().__init__("The pool declined the exact-output sale")
        self.slot = slot


def account(key, writable=False, signer=False):
    return AccountMeta(key, signer, writable)


def pool_accounts(pool, slot, rpc):
    result = rpc("getAccountInfo", [str(pool), {"encoding": "base64", "commitment": "confirmed", "minContextSlot": slot}])
    value = result["value"]
    data = base64.b64decode(value["data"][0], validate=True)
    if (value["owner"] != str(ORCA) or value["executable"] or result["context"]["slot"] < slot or len(data) < 245
            or data[:8] != hashlib.sha256(b"account:Whirlpool").digest()[:8]
            or data[101:133] != bytes(credit.SOL) or data[181:213] != bytes(devnet.MINT)):
        raise ValueError("Unexpected Orca pool or token pair")
    span = struct.unpack_from("<H", data, 41)[0] * 88
    if not span:
        raise ValueError("Invalid Whirlpool tick spacing")
    start = struct.unpack_from("<i", data, 81)[0] // span * span
    arrays = [Pubkey.find_program_address([b"tick_array", bytes(pool), str(start - i*span).encode()], ORCA)[0] for i in range(3)]
    oracle = Pubkey.find_program_address([b"oracle", bytes(pool)], ORCA)[0]
    return (pool, Pubkey.from_bytes(data[133:165]), Pubkey.from_bytes(data[213:245]), *arrays, oracle), result["context"]["slot"]


def swap_instructions(payer, cash, route, output, maximum, rent):
    ops = []
    pool, sol_vault, usd_vault, *ticks = route
    wrapped = Pubkey.create_with_seed(payer, SEED, devnet.TOKEN)
    ops.append(create_account_with_seed(CreateAccountWithSeedParams(from_pubkey=payer, to_pubkey=wrapped, base=payer,
               seed=SEED, lamports=rent + maximum, space=165, owner=devnet.TOKEN)))
    ops.append(Instruction(devnet.TOKEN, b"\x12" + bytes(payer), [account(wrapped, True), account(credit.SOL)]))
    data = hashlib.sha256(b"global:swap").digest()[:8] + struct.pack("<QQ", output, maximum) + bytes(16) + bytes((0, 1))
    ops.append(Instruction(ORCA, data, [account(devnet.TOKEN), account(payer, signer=True), account(pool, True),
               account(wrapped, True), account(sol_vault, True), account(cash, True), account(usd_vault, True),
               *[account(key, True) for key in ticks]]))
    ops.append(Instruction(devnet.TOKEN, b"\x09", [account(wrapped, True), account(payer, True), account(payer, signer=True)]))
    return ops


def instructions(plan, route, output, maximum, rent, reserve, customer, follow):
    ops = swap_instructions(plan.payer, plan.cash, route, output, maximum, rent) if output else []
    burn = reserve["eligible_shares_raw"] - reserve["retained_shares_raw"]
    if burn:
        ops.append(devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, burn, venue=devnet.VENUE))
    vendor = (reserve["fuel_cents"] - customer) * reserve["raw_units_per_cent"]
    ops.append(public_offer.transfer(plan.cash, plan.payer, plan.destination, vendor))
    if follow:
        ops.append(devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, reserve["retained_shares_raw"], venue=devnet.VENUE))
    return ops


def simulate(plan, route, output, maximum, rent, reserve, customer, wallet, slot, rpc, *, follow=False):
    ops = instructions(plan, route, output, maximum, rent, reserve, customer, follow)
    config = {"commitment": "confirmed", "minContextSlot": slot}
    block = rpc("getLatestBlockhash", [config])["value"]["blockhash"]
    message = MessageV1.try_compile(plan.payer, ops, Hash.from_string(block), TransactionConfig(compute_unit_limit=600000, loaded_accounts_data_size_limit=16 * 1024 * 1024, heap_size=32768))
    wire = bytes(VersionedTransaction.populate(message, [Signature.default()]))
    if len(wire) > 4096:
        raise ValueError("The exact-output payment exceeds the V1 transaction limit")
    result = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {**config, "encoding": "base64", "sigVerify": False}])
    value, keys = result["value"], list(message.account_keys)
    if result["context"]["slot"] < slot:
        raise ValueError("The payment check predates its pool quote")
    slot = result["context"]["slot"]
    if value["err"] is not None:
        failure = value["err"].get("InstructionError", []) if isinstance(value["err"], dict) else []
        expected = value["preBalances"].copy()
        expected[keys.index(plan.payer)] -= value["fee"]
        if (output and len(failure) == 2 and failure[0] == 2 and ops[2].program_id == ORCA
                and any(line.startswith(f"Program {ORCA} failed:") for line in value["logs"])
                and expected == value["postBalances"] and value["preTokenBalances"] == value["postTokenBalances"]):
            raise PoolDeclined(slot)
        raise ValueError("The vendor payment could not be verified")
    if plan.shares in keys:
        balances = next_stop.balances(plan, keys, value)
    else:
        balances = {"shares": (0, 0), "custody": (0, 0)}
        for name, address, owner in (("cash", plan.cash, plan.payer), ("vendor", plan.destination, plan.payee)):
            before, after = devnet.token_balances(value, keys, address, devnet.MINT, owner)
            balances[name] = (before, after - before)
    i = keys.index(plan.payer)
    sold = value["preBalances"][i] - value["postBalances"][i] - value["fee"]
    scale = reserve["raw_units_per_cent"]
    vendor = (reserve["fuel_cents"] - customer) * scale
    used = vendor - reserve["vendor_raw"] - output
    returned = -balances["custody"][1] - reserve["vendor_raw"] - reserve["wallet_increase_raw"]
    if (value["preBalances"][i] != wallet["lamports"] or balances["cash"][0] != int(wallet["usdc_raw"]) or used < 0 or used > balances["cash"][0]
            or balances["cash"][1] != reserve["wallet_increase_raw"] - used + returned or balances["vendor"][1] != vendor
            or balances["shares"][1] != -reserve["eligible_shares_raw"] + (0 if follow else reserve["retained_shares_raw"])
            or (returned < reserve["holdback"]["cost_cents"] * scale if follow else returned != 0)
            or not 0 <= sold <= maximum or bool(sold) != bool(output)):
        raise ValueError("The wallet, vault, next stop and vendor amounts do not reconcile")
    if output:
        pool, sol_vault, usd_vault, *_ = route
        before, after = devnet.token_balances(value, keys, usd_vault, devnet.MINT, pool)
        j = keys.index(sol_vault)
        k = keys.index(Pubkey.create_with_seed(plan.payer, SEED, devnet.TOKEN))
        if (before - after != output or value["postBalances"][j] - value["preBalances"][j] != sold
                or value["preBalances"][k] or value["postBalances"][k]):
            raise ValueError("Orca pool proceeds or temporary SOL account do not match the quote")
    return {"pool": str(route[0]) if route else None, "spent_lamports": sold, "wallet_raw": used, "swap_raw": output,
            "reserve_raw": reserve["vendor_raw"], "vendor_raw": vendor, "retained_raw": returned, "slot": slot,
            "fee_lamports": value["fee"], "limit_lamports": maximum, "rent_lamports": rent if output else 0,
            "entry_lamports": maximum + (rent if output else 0) + value["fee"]}


def spendable_reserve(reserve, customer, wallet_raw, rpc):
    scale = reserve["raw_units_per_cent"]
    needed = max(0, reserve["fuel_cents"] - customer - wallet_raw // scale)
    if needed * scale >= reserve["vendor_raw"]:
        return reserve
    if not needed:
        return {**reserve, "vendor_raw": 0, "retained_shares_raw": reserve["eligible_shares_raw"], "wallet_increase_raw": 0}
    smaller = next_stop.preview(*reserve_credit.prior_payments(), needed, 0, rpc, holdback=reserve["holdback"])
    if smaller["vendor_raw"] != needed * scale or smaller["eligible_shares_raw"] != reserve["eligible_shares_raw"]:
        raise ValueError("The smaller reserve payment changed during the wallet check")
    return {**smaller, "fuel_cents": reserve["fuel_cents"]}


def price(reserve, customer, rpc):
    if customer not in customer_credit.ADVANCES or rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("Choose a supported contribution on public Devnet")
    plan = next_stop.payment()
    wallet = devnet.wallet_balances(str(plan.payer), rpc)
    loan_reserve = reserve
    reserve = spendable_reserve(reserve, customer, int(wallet["usdc_raw"]), rpc)
    slot = max(wallet["slot"], reserve["slot"])
    scale = reserve["raw_units_per_cent"]
    gap = (reserve["fuel_cents"] - customer) * scale - reserve["vendor_raw"]
    output = max(0, gap - int(wallet["usdc_raw"]) // scale * scale)
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    rows, declined = [], []
    for pool in POOLS if output else (None,):
        route, slot = pool_accounts(pool, slot, rpc) if pool else (None, slot)
        probe = min(credit.LAMPORTS, wallet["lamports"] - rent - 10000) if output else 0
        if probe <= 0 and output:
            raise ValueError("The demo wallet cannot fund the temporary swap account")
        try:
            q = simulate(plan, route, output, probe, rent, reserve, customer, wallet, slot, rpc)
            maximum = (q["spent_lamports"] * 10050 + 9999) // 10000
            bounded = simulate(plan, route, output, maximum, rent, reserve, customer, wallet, q["slot"], rpc)
            follow = simulate(plan, route, output, maximum, rent, reserve, customer, wallet, bounded["slot"], rpc, follow=True)
            if follow["spent_lamports"] != bounded["spent_lamports"] or follow["fee_lamports"] != bounded["fee_lamports"]:
                raise ValueError("The pool moved while checking the next fuel stop")
            rows.append({**bounded, "retained_raw": follow["retained_raw"], "remainder_slot": follow["slot"]})
            slot = follow["slot"]
        except PoolDeclined as error:
            slot = error.slot
            declined.append(str(pool))
    if not rows:
        raise ValueError("No checked pool could fill the exact-output sale")
    payment = customer_credit.residual(loan_reserve, customer)
    loan = atomic_fuel.terms({**payment, "slot": slot}, customer_credit.DROP, customer_credit.DAYS, rpc, protect=True)
    boundary = customer_credit.check_entry(payment, loan, rpc)
    if boundary > loan_reserve["slot"] + 240:
        raise ValueError("The pool observations are too far from the vault observation")
    return {"routes": rows, "declined": declined, "loan": loan, "entry_slot": boundary, "wallet": wallet, "scale": scale, "customer_cents": customer, "holdback": reserve["holdback"]}


def capture(customer, rpc=devnet.request):
    data = {"customer_cents": customer, "reserve": [], "swap": []}
    reserve = reserve_credit.reserve_leg(atomic_fuel.record(data["reserve"], rpc))
    result = price(reserve, customer, atomic_fuel.record(data["swap"], rpc))
    return result, data


def replay(data, customer):
    if data["customer_cents"] != customer:
        raise ValueError("The customer payment differs from the recorded sale")
    reserve = reserve_credit.replay_reserve(data["reserve"])
    return atomic_fuel.replay_calls(data["swap"], lambda rpc: price(reserve, customer, rpc))


@cache
def saved(customer):
    return replay(json.loads(gzip.decompress(SAMPLE.read_bytes()))[str(customer)], customer)


def compare(quote, fee, funds, extra, payday_price=None):
    customer_credit.inputs("both", fee, funds, extra)
    loan, scale, customer = quote["loan"], quote["scale"], quote["customer_cents"]
    reference = int(Decimal(loan["sol_price"]) * 100)
    price_cents = reference if payday_price is None else customer_credit.integer(payday_price, "replacement price per SOL", 10000000)
    funds = min(funds, quote["wallet"]["lamports"])
    routes = quote["routes"]
    fits = [r for r in routes if r["entry_lamports"] <= funds]
    sell = min(fits or routes, key=lambda r: (r["spent_lamports"] + r["fee_lamports"], r["entry_lamports"]))
    net_sold = sell["spent_lamports"] + sell["fee_lamports"] - loan["fee_lamports"]
    if net_sold < 0:
        raise ValueError("Unsupported network fee differential")
    cash = sell["wallet_raw"] // scale
    repay = loan["payday_stress"]["repay_cents"] + extra
    denom = credit.LAMPORTS * scale * 100
    replacement = (net_sold * price_cents * 10**6 + denom - 1) // denom
    refill_difference = (loan["reserve_raw"] - sell["reserve_raw"]) // scale
    threshold = max(0, (repay + refill_difference - cash) * denom // (net_sold * 10**6) + 1) if net_sold else None
    invoice = reserve_credit.INVOICE_CENTS - customer - customer_credit.customer_fee(customer, fee)
    after_refill = invoice - sell["reserve_raw"] // scale
    entry = customer_credit.entry_budget(loan)
    return {"sale": sell, "declined": quote["declined"], "pools": [{"pool": r["pool"], "spent_lamports": r["spent_lamports"], "entry_lamports": r["entry_lamports"]} for r in routes],
            "loan_entry": entry, "sale_short_lamports": max(0, sell["entry_lamports"] - funds),
            "loan_short_lamports": max(0, entry - funds), "available_lamports": funds, "wallet_slot": quote["wallet"]["slot"],
            "customer_cents": customer, "scale": scale, "repay_cents": repay, "buyback_cents": replacement,
            "loan_remaining_cents": after_refill - refill_difference - repay, "sale_remaining_cents": after_refill - cash - replacement,
            "sale_cash_after_refill_cents": after_refill, "difference_cents": repay + refill_difference - cash - replacement,
            "first_higher_cents": threshold, "reference_cents": reference, "price_cents": price_cents,
            "holdback_cents": quote["holdback"]["cost_cents"], "entry_slot": quote["entry_slot"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay the vendor payment through Orca and the matched Kamino loan")
    parser.add_argument("evidence", type=Path, nargs="?")
    parser.add_argument("--customer-cents", type=int, choices=customer_credit.ADVANCES, default=9900)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.evidence and args.live:
        parser.error("Choose a downloaded check or a live simulation")
    if args.live:
        checked = capture(args.customer_cents)[0]
    elif args.evidence:
        checked = replay(json.loads(args.evidence.read_bytes())[str(args.customer_cents)], args.customer_cents)
    else:
        checked = saved(args.customer_cents)
    print(json.dumps(compare(checked, 100, 60000000, 0), indent=2))
