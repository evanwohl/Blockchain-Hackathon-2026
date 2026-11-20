"""Simulate a secured KLend draw for a vendor payment or a multi-stop fuel budget."""
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


def transaction(collateral, block, advance=ADVANCE, *, destination="vendor"):
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
        if program == KLEND and data[:8] == bytes.fromhex("a1808ff5abc7c206"):
            data = data[:8] + struct.pack("<Q", advance)
        accounts = [AccountMeta(Pubkey.from_string(address), role >= 2, role in (1, 3)) for address, role in row["a"]]
        instructions.append(Instruction(program, data, accounts))
    if destination == "vendor":
        transfer = [AccountMeta(associated(OWNER, devnet.MINT), False, True), AccountMeta(devnet.MINT, False, False),
                    AccountMeta(vendor, False, True), AccountMeta(OWNER, True, False)]
        instructions.append(Instruction(TOKEN, struct.pack("<BQB", 12, advance, 6), transfer))
    elif destination != "wallet":
        raise ValueError("Unknown loan destination")
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


def evaluate(run, drop, days, advance=ADVANCE, *, step=STEP, destination="vendor"):
    value = run["response"]["value"]
    if value["err"] is not None:
        raise ValueError("Kamino declined this collateralized payment")
    collateral = run["collateral"]
    tx = transaction(collateral, run["block"], advance, destination=destination)
    if destination == "wallet" and base64.b64decode(run["wire"], validate=True) != bytes(tx):
        raise ValueError("Wallet draw differs from the simulated transaction")
    keys = list(tx.message.account_keys)
    proof = json.loads(devnet.SAMPLE.read_text())
    vendor = Pubkey.from_string(proof["decision"]["vendor"])
    owner_cash = associated(OWNER, devnet.MINT)
    def change(account, owner):
        before, after = devnet.token_balances(value, keys, account, devnet.MINT, owner)
        return after - before
    retained = advance if destination == "wallet" else 0
    if change(owner_cash, OWNER) != retained or (destination == "vendor" and change(vendor, Pubkey.from_string(proof["vendor_owner"])) != advance):
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
    if destination == "wallet":
        authority, _ = Pubkey.find_program_address([b"lma", market], KLEND)
        supplied = -change(Pubkey.from_bytes(debt[160:192]), authority)
        fee_gain = change(Pubkey.from_bytes(debt[192:224]), authority)
        if vendor in keys or fee_gain < 0 or supplied != retained + fee_gain:
            raise ValueError("Lending reserve and wallet proceeds do not reconcile")
    price = Decimal(uint(backing, 248)) / UNIT
    stable_price = Decimal(uint(debt, 248)) / UNIT
    pledged = Decimal(uint(obligation, 1192)) / UNIT
    principal = Decimal(uint(obligation, 1296)) / UNIT
    if price <= 0 or stable_price <= 0 or abs(pledged - price * collateral / LAMPORTS) > Decimal(".00001") or principal < advance:
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
    minimum = ceiling(needed / price * LAMPORTS / step) * step
    scale = int(proof["decision"]["raw_units_per_cent"])
    cost = ceiling((repayment - advance) / scale)
    fee = value["fee"]
    setup = value["preBalances"][keys.index(OWNER)] - value["postBalances"][keys.index(OWNER)] - collateral - fee
    if fee < 0 or setup < 0:
        raise ValueError("Unexpected loan setup balance change")
    return {"destination": destination, "wallet_gain_raw": retained, "slot": run["response"]["context"]["slot"], "checked_at": run["checked_at"], "days": days, "drop": drop,
            "advance_raw": advance, "collateral_lamports": collateral, "minimum_lamports": minimum,
            "current_ltv_bps": ceiling(10000 * principal / 10**6 * stable_price * factor / pledged),
            "stress_ltv_bps": ceiling(10000 * adjusted_debt / stressed), "liquidation_pct": liquidation,
            "remaining_liquidity_raw": uint(debt, 224, 8),
            "repayment_factor": str(repayment / advance), "rate_bps": ceiling(apr * 10000), "repay_cents": ceiling(repayment / scale), "cost_cents": cost,
            "advance_cents": advance // scale, **({"fee_headroom_cents": 1000 - cost} if advance == ADVANCE else {}),
            "liquidation_price_cents": ceiling(100 * adjusted_debt * price / (pledged * liquidation / 100)),
            "entry_cash_cents": ceiling((fee + setup) * price * 100 / LAMPORTS),
            "fee_lamports": fee, "setup_lamports": setup, "sol_price": str(price.quantize(Decimal(".01")))}


def simulate(collateral, rpc, min_slot=0, advance=ADVANCE, *, destination="vendor"):
    block = rpc("getLatestBlockhash", [{"commitment": "confirmed", "minContextSlot": min_slot}])["value"]["blockhash"]
    wire = base64.b64encode(bytes(transaction(collateral, block, advance, destination=destination))).decode()
    addresses = [route()[key] for key in ("obligation", "usd", "sol")]
    response = rpc("simulateTransaction", [wire,
                   {"encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": True, "commitment": "confirmed",
                    "minContextSlot": min_slot, "accounts": {"encoding": "base64", "addresses": addresses}}])
    return {"response": response, "collateral": collateral, "block": block, "wire": wire, "checked_at": datetime.now(timezone.utc).isoformat()}


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


def trip_draw(gap_cents, pledge_millis, drop, days, rpc=devnet.request, *, recorded=False, destination="vendor", repay_limit=None):
    terms(drop, days)
    if destination not in ("wallet", "vendor"):
        raise ValueError("Unknown loan destination")
    if type(gap_cents) is not int or not 0 < gap_cents <= 100_000_000:
        raise ValueError("Enter a positive fuel shortfall")
    if type(pledge_millis) is not int or not 0 <= pledge_millis <= 1_000_000:
        raise ValueError("Enter a SOL pledge limit between 0 and 1,000")
    if repay_limit is not None and (destination != "wallet" or type(repay_limit) is not int or not 0 <= repay_limit <= 100_000_000):
        raise ValueError("Enter a valid wallet loan repayment limit")
    result = {"gap_cents": gap_cents, "remaining_cents": gap_cents, "loan": None}
    if repay_limit is not None:
        result["repay_limit_cents"] = repay_limit
        if repay_limit == 0:
            return {**result, "repayment_limited": True}
    if not pledge_millis:
        return result
    scale = json.loads(devnet.SAMPLE.read_text())["decision"]["raw_units_per_cent"]
    model_ratio = 10_000 // scale
    step = LAMPORTS // (1000 * model_ratio)
    cap = pledge_millis * step

    def funded(quote):
        pledged = quote["collateral_lamports"]
        cents = quote["advance_cents"]
        if not (0 < cents <= gap_cents and quote["minimum_lamports"] <= pledged <= cap and pledged % step == 0):
            raise ValueError("Loan falls outside the trip limits")
        if repay_limit is not None and quote["repay_cents"] > repay_limit:
            raise ValueError("The simulated repayment exceeds the payday limit")
        quote.pop("fee_headroom_cents", None)
        sale_price = Decimal(quote["sol_price"])
        holding_cost = quote["entry_cash_cents"] + quote["cost_cents"]
        quote.update(pledge_millis=pledged // step, model_ratio=model_ratio,
                     buyback_break_even_cents=ceiling(100 * sale_price * (cents + holding_cost) / cents))
        remaining = gap_cents - cents
        response = {**result, "remaining_cents": remaining, "loan": quote}
        if remaining and not result.get("repayment_limited") and remaining * scale <= quote["remaining_liquidity_raw"]:
            unit = cents * step
            estimate = (quote["minimum_lamports"] * gap_cents + unit - 1) // unit
            if pledge_millis < estimate <= 1_000_000:
                response["estimated_full_pledge_millis"] = estimate
        return response

    if recorded:
        if destination == "wallet":
            filename, key = "payday_credit.json.gz", f"{gap_cents}-{pledge_millis}-{drop}-{days}"
        else:
            filename, key = "trip_credit.json.gz", f"{gap_cents}-{pledge_millis}"
            if (drop, days) != (40, 23):
                raise ValueError("No saved check for these terms")
        samples = json.loads(gzip.decompress((DATA / filename).read_bytes()))
        if destination == "wallet" and repay_limit is not None and f"{key}-{repay_limit}" in samples:
            key = f"{key}-{repay_limit}"
        if key not in samples:
            raise ValueError("No saved check for these terms")
        sample = samples[key]
        quote = evaluate(sample["run"], drop, days, sample["cents"] * scale, step=1, destination=destination)
        if repay_limit is not None:
            result["repayment_limited"] = gap_cents * Decimal(quote["repayment_factor"]) > repay_limit
        return funded(quote)
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("This preview requires public Solana Devnet")
    cents = 100
    observation = simulate(STEP, rpc, advance=cents * scale, destination=destination)
    for attempt in range(5):
        quote = evaluate(observation, drop, days, cents * scale, step=1, destination=destination)
        required = quote["minimum_lamports"]
        projected = min(gap_cents, cap * cents // required)
        projected = min(projected, cents + quote["remaining_liquidity_raw"] // scale)
        if repay_limit is not None:
            affordable = int(Decimal(repay_limit) / Decimal(quote["repayment_factor"]))
            result["repayment_limited"] = gap_cents > affordable
            projected = min(projected, affordable)
        if attempt:
            projected = min(projected, cents)
        if not projected:
            return result
        pledge = min(cap, ((required * projected + cents * step - 1) // (cents * step)) * step)
        if cents == projected and pledge == observation["collateral"] and pledge >= required:
            return funded(quote)
        observation = simulate(pledge, rpc, quote["slot"], projected * scale, destination=destination)
        cents = projected
    raise ValueError("The market moved while checking the pledge limit")


def compare_fee(result, offer_cents):
    loan = result["loan"]
    if loan is None or result["remaining_cents"]:
        return None
    entry_and_financing = loan["entry_cash_cents"] + loan["cost_cents"]
    return {"entry_and_financing_cents": entry_and_financing,
            "room_cents": None if offer_cents is None else offer_cents - entry_and_financing}
