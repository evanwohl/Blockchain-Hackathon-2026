"""Simulate a secured KLend draw paid directly to the sample fuel vendor."""
import base64
import gzip
import json
import struct
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from functools import cache
from pathlib import Path

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from freightos import devnet
from freightos.reserve import TOKEN, associated

DATA = Path(__file__).with_name("artifacts") / "devnet"
KLEND = Pubkey.from_string("KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD")
OWNER = Pubkey.from_string("51sZJV6SFRiE1CZwwH1sbwokcrJDuFpFwHDfqoJpxBFp")
SOL = Pubkey.from_string("So11111111111111111111111111111111111111112")
UNIT = Decimal(2**60)
LAMPORTS = 10**9
ADVANCE = 1_900_000
SEED_COLLATERAL = 100_000_000
STEP = 1_000_000


def ceiling(value):
    return int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


@cache
def route():
    return json.loads((DATA / "credit_route.json").read_text())


def terms(drop, days):
    if type(drop) is not int or drop not in (20, 40, 60) or type(days) is not int or days not in (23, 30):
        raise ValueError("Choose one of the displayed collateral stresses and payment dates")


def transaction(collateral, block):
    proof = json.loads(devnet.SAMPLE.read_text())
    vendor = Pubkey.from_string(proof["decision"]["vendor"])
    instructions = []
    for row in route()["ixs"]:
        program = Pubkey.from_string(row["p"])
        if str(program) == "ComputeBudget111111111111111111111111111111":
            continue
        data = base64.b64decode(row["d"])
        if row["p"] == "11111111111111111111111111111111" and data[:4] == struct.pack("<I", 2):
            wrapped = int.from_bytes(data[4:], "little") - SEED_COLLATERAL + collateral
            data = data[:4] + struct.pack("<Q", wrapped)
        if program == KLEND and data[:8] == bytes.fromhex("d8e0bf1bcc9766af"):
            data = data[:8] + struct.pack("<Q", collateral)
        accounts = [AccountMeta(Pubkey.from_string(address), role >= 2, role in (1, 3)) for address, role in row["a"]]
        instructions.append(Instruction(program, data, accounts))
    transfer = [AccountMeta(associated(OWNER, devnet.MINT), False, True), AccountMeta(devnet.MINT, False, False),
                AccountMeta(vendor, False, True), AccountMeta(OWNER, True, False)]
    instructions.append(Instruction(TOKEN, struct.pack("<BQB", 12, ADVANCE, 6), transfer))
    message = MessageV0.try_compile(OWNER, instructions, [], Hash.from_string(block))
    return VersionedTransaction.populate(message, [Signature.default()])


def read_program_account(account, discriminator, size):
    data = base64.b64decode(account["data"][0], validate=True)
    if account["owner"] != str(KLEND) or len(data) != size or data[:8] != bytes.fromhex(discriminator):
        raise ValueError("Unexpected Kamino account layout")
    return data


def uint(data, offset, length=16):
    return int.from_bytes(data[offset:offset + length], "little")


def configured_rate(reserve):
    borrowed = Decimal(uint(reserve, 232)) / UNIT
    fees = sum(Decimal(uint(reserve, 128 + n)) / UNIT for n in (216, 232, 248))
    supply = uint(reserve, 224, 8) + borrowed - fees
    utilization = borrowed * 10000 / supply
    points = list(struct.iter_unpack("<II", reserve[4920:5008]))
    for (lo, low_rate), (hi, high_rate) in zip(points, points[1:]):
        if lo <= utilization <= hi and hi > lo:
            return (low_rate + (high_rate - low_rate) * (utilization - lo) / (hi - lo) + uint(reserve, 4858, 2)) / 10000
    raise ValueError("Borrow utilization is outside the Kamino rate curve")


def evaluate(run, drop, days):
    value = run["response"]["value"]
    if value["err"] is not None:
        raise ValueError("Kamino declined this collateralized payment")
    collateral = run["collateral"]
    message = transaction(collateral, run["block"]).message
    keys = list(message.account_keys)
    proof = json.loads(devnet.SAMPLE.read_text())
    vendor = Pubkey.from_string(proof["decision"]["vendor"])
    owner_cash = associated(OWNER, devnet.MINT)
    def change(account, owner):
        before, after = devnet.token_balances(value, keys, account, devnet.MINT, owner)
        return after - before
    if change(vendor, Pubkey.from_string(proof["vendor_owner"])) != ADVANCE or change(owner_cash, OWNER) != 0:
        raise ValueError("Borrow proceeds and fuel payment do not reconcile")
    obligation, debt, backing = [read_program_account(a, disc, size) for a, disc, size in zip(value["accounts"],
                                 ("a8ce8d6a584caca7", "2bf2ccca1af73b7f", "2bf2ccca1af73b7f"), (3344, 8624, 8624))]
    market = bytes(Pubkey.from_string(route()["market"]))
    if (obligation[32:64] != market or obligation[64:96] != bytes(OWNER) or debt[32:64] != market or backing[32:64] != market
            or obligation[96:128] != bytes(Pubkey.from_string(route()["sol"]))
            or obligation[1208:1240] != bytes(Pubkey.from_string(route()["usd"]))
            or debt[128:160] != bytes(devnet.MINT) or backing[128:160] != bytes(SOL)
            or value["preBalances"][keys.index(Pubkey.from_string(route()["obligation"]))] != 0):
        raise ValueError("Loan market, obligation, or assets differ from the sample")
    price = Decimal(uint(backing, 248)) / UNIT
    stable_price = Decimal(uint(debt, 248)) / UNIT
    pledged = Decimal(uint(obligation, 1192)) / UNIT
    principal = Decimal(uint(obligation, 1296)) / UNIT
    if price <= 0 or stable_price <= 0 or abs(pledged - price * collateral / LAMPORTS) > Decimal(".00001") or principal < ADVANCE:
        raise ValueError("Debt or collateral value differs from the payment")
    apr = configured_rate(debt)
    repayment = principal * (apr * days / 365).exp()
    factor = Decimal(uint(debt, 5008, 8)) / 100
    initial, liquidation = backing[4872], backing[4873]
    if not 10 < liquidation <= 100 or not 0 < initial < liquidation or factor < 1:
        raise ValueError("Unsupported Kamino collateral configuration")
    stressed = pledged * (100 - drop) / 100
    adjusted_debt = repayment / 10**6 * stable_price * factor
    needed = max(principal / 10**6 * stable_price * factor / (Decimal(initial)/100),
                 adjusted_debt / ((Decimal(100-drop)/100) * (Decimal(liquidation-10)/100)))
    minimum = ceiling(needed / price * LAMPORTS / STEP) * STEP
    scale = int(proof["decision"]["raw_units_per_cent"])
    cost = ceiling((repayment - ADVANCE) / scale)
    fee = value["fee"]
    setup = value["preBalances"][keys.index(OWNER)] - value["postBalances"][keys.index(OWNER)] - collateral - fee
    return {"slot": run["response"]["context"]["slot"], "checked_at": run["checked_at"], "days": days, "drop": drop,
            "advance_raw": ADVANCE, "collateral_lamports": collateral, "minimum_lamports": minimum,
            "current_ltv_bps": ceiling(10000 * principal / 10**6 * stable_price * factor / pledged),
            "stress_ltv_bps": ceiling(10000 * adjusted_debt / stressed), "liquidation_pct": liquidation,
            "rate_bps": ceiling(apr * 10000), "repay_cents": ceiling(repayment / scale), "cost_cents": cost,
            "advance_cents": ADVANCE // scale, "fee_headroom_cents": 1000 - cost,
            "liquidation_price_cents": ceiling(100 * adjusted_debt * price / (pledged * liquidation / 100)),
            "fee_lamports": fee, "setup_lamports": setup, "sol_price": str(price.quantize(Decimal(".01")))}


def simulate(collateral, rpc, min_slot=0):
    block = rpc("getLatestBlockhash", [{"commitment": "confirmed", "minContextSlot": min_slot}])["value"]["blockhash"]
    tx = transaction(collateral, block)
    addresses = [route()[key] for key in ("obligation", "usd", "sol")]
    response = rpc("simulateTransaction", [base64.b64encode(bytes(tx)).decode(),
                   {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed",
                    "minContextSlot": min_slot, "accounts": {"encoding": "base64", "addresses": addresses}}])
    return {"response": response, "collateral": collateral, "block": block, "checked_at": datetime.now(timezone.utc).isoformat()}


def capture(drop, days, rpc=devnet.request):
    terms(drop, days)
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("This sample requires public Solana Devnet")
    observation = simulate(SEED_COLLATERAL, rpc)
    for _ in range(3):
        quote = evaluate(observation, drop, days)
        if quote["minimum_lamports"] == quote["collateral_lamports"]:
            return observation
        observation = simulate(quote["minimum_lamports"], rpc, quote["slot"])
    raise ValueError("Collateral price moved during the simulation")


def saved(drop, days):
    terms(drop, days)
    return json.loads(gzip.decompress((DATA / "credit_examples.json.gz").read_bytes()))[f"{drop}-{days}"]
