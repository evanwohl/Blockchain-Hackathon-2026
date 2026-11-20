"""Replay a cash-funded partial repayment on an open Devnet Kamino obligation."""
import base64
import gzip
import hashlib
import json
import struct
from functools import cache

from solders.hash import Hash
from solders.instruction import Instruction
from solders.message import MessageV1, TransactionConfig
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from freightos import atomic_fuel, collateral_path, devnet, freight_credit as credit, fuel_swap, load_credit, mainnet_loan, midtrip, public_offer, repeat_credit, split_payment as bill, stop_backup

from hedging.quick_pay import integer

SCALE = 100
PAYMENT = 106_800
DOWNSIDE_PAYMENT = 144_700
DOWNSIDE_SAMPLE = credit.DATA / "downside_payment.json.gz"
SAMPLE = credit.DATA / "partial_fuel_payment.json.gz"


def simulate(rpc=devnet.request, *, payment=PAYMENT, extra_pledge=0):
    if type(extra_pledge) is not int or not 0 <= extra_pledge <= 10_000_000:
        raise ValueError("Enter a valid sample collateral top-up")
    if type(payment) is not int or not 0 <= payment < bill.LOAN or not (payment or extra_pledge):
        raise ValueError("Enter a partial payment below the sample principal")
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("The partial repayment requires Solana Devnet")
    owner, obligation, _, _, cash = repeat_credit.accounts()
    sponsor = credit.OWNER
    sponsor_cash = load_credit.borrower_accounts(sponsor)[4]
    route, slot = fuel_swap.pool_accounts(bill.POOL, 0, rpc)
    block = rpc("getLatestBlockhash", [{"commitment": "confirmed", "minContextSlot": slot}])
    slot = block["context"]["slot"]
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    close = load_credit.instructions(owner, bill.PLEDGE, bill.LOAN, repay=True)
    combined = close[-2].accounts
    accounts = [*combined[:9], combined[25], combined[26], combined[12], combined[27]]
    tag = hashlib.sha256(b"global:repay_obligation_liquidity_v2").digest()[:8]
    repay = Instruction(credit.KLEND, tag + struct.pack("<Q", payment), accounts)
    vendor_owner = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])

    def run(entry_cap, sponsor_cap):
        ops = bill.opening_instructions(owner, cash, route, entry_cap, rent)
        if extra_pledge:
            topup = load_credit.instructions(owner, extra_pledge, bill.LOAN)
            deposit, = [ix for ix in topup if ix.program_id == credit.KLEND and ix.data[:8] == bytes.fromhex("d8e0bf1bcc9766af")]
            ops.extend([*topup[:3], *close[1:-2], deposit, topup[-2]])
        if payment:
            ops.extend(fuel_swap.swap_instructions(sponsor, sponsor_cash, route, payment, sponsor_cap, rent))
            ops.extend([public_offer.transfer(sponsor_cash, sponsor, cash, payment), *close[1:-2], repay])
        config = TransactionConfig(compute_unit_limit=1_000_000, loaded_accounts_data_size_limit=16*1024*1024, heap_size=32768)
        msg = MessageV1.try_compile(sponsor, ops, Hash.from_string(block["value"]["blockhash"]), config)
        wire = bytes(VersionedTransaction.populate(msg, [Signature.default()] * msg.header.num_required_signatures))
        if len(wire) > 4096:
            raise ValueError("Partial repayment exceeds the Devnet v1 message limit")
        result = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {
            "commitment": "confirmed", "minContextSlot": slot, "encoding": "base64", "sigVerify": False,
            "innerInstructions": True, "accounts": {"encoding": "base64", "addresses": [str(obligation)]}}])
        v = result["value"]
        if result["context"]["slot"] < slot or v["err"] is not None:
            raise ValueError("Public Devnet could not verify this partial repayment")
        keys = list(msg.account_keys)
        record = {"meta": v}
        authority = combined[12].pubkey
        def token(address, holder):
            return devnet.token_delta(record, keys, address, devnet.MINT, holder)
        moves = {g["index"]: g["instructions"] for g in v["innerInstructions"]}
        def transfers(index, kind):
            return [ix["parsed"]["info"] for ix in moves[index] if ix["programId"] == str(devnet.TOKEN)
                    and ix.get("parsed", {}).get("type") == kind]
        spends = []
        swaps = [(owner, cash, bill.SALE, entry_cap)]
        if payment:
            swaps.append((sponsor, sponsor_cash, payment, sponsor_cap))
        for i, (payer, dest, output, cap) in zip((i for i, ix in enumerate(ops) if ix.program_id == fuel_swap.ORCA), swaps, strict=True):
            a, b = transfers(i, "transfer")
            spent = int(a["amount"])
            if (a["source"] != str(Pubkey.create_with_seed(payer, fuel_swap.SEED, devnet.TOKEN))
                    or a["destination"] != str(route[1]) or not 0 < spent <= cap
                    or b["source"] != str(route[2]) or b["destination"] != str(dest) or int(b["amount"]) != output):
                raise ValueError("Orca did not deliver the funding under its sale limit")
            spends.append(spent)
        deposits = [i for i, ix in enumerate(ops) if ix.program_id == credit.KLEND and ix.data[:8] == bytes.fromhex("d8e0bf1bcc9766af")]
        shares = 0
        for index, expected in zip(deposits, [bill.PLEDGE, extra_pledge] if extra_pledge else [bill.PLEDGE], strict=True):
            minted, = transfers(index, "mintTo")
            posted, = transfers(index, "transferChecked")
            if (minted["account"] != str(combined[15].pubkey) or minted["mint"] != str(combined[16].pubkey)
                    or minted["mintAuthority"] != str(authority) or int(minted["amount"]) <= 0
                    or posted["source"] != str(load_credit.borrower_accounts(owner)[3]) or posted["destination"] != str(combined[17].pubkey)
                    or posted["mint"] != str(credit.SOL) or int(posted["tokenAmount"]["amount"]) != expected):
                raise ValueError("The collateral deposit or its minted shares differs from the plan")
            shares += int(minted["amount"])
        if payment:
            leg, = transfers(len(ops)-1, "transferChecked")
            if (leg["source"] != str(cash) or leg["destination"] != str(combined[5].pubkey)
                    or leg["mint"] != str(devnet.MINT) or int(leg["tokenAmount"]["amount"]) != payment
                    or token(sponsor_cash, sponsor)):
                raise ValueError("The early payment differs from the plan")
        data = credit.read_program_account(v["accounts"][0], "a8ce8d6a584caca7", 3344)
        remaining = bill.LOAN - payment
        if (token(combined[5].pubkey, authority) != -remaining or token(load_credit.vendor(), vendor_owner) != bill.LOAN + bill.SALE
                or token(cash, owner)
                or load_credit.token_change(record, keys, combined[17].pubkey, credit.SOL, authority, 9) != bill.PLEDGE + extra_pledge
                or v["preBalances"][keys.index(obligation)] or data[64:96] != bytes(owner)
                or data[32:64] != bytes(combined[2].pubkey) or data[96:128] != bytes(combined[13].pubkey)
                or shares != credit.uint(data, 128, 8)
                or data[1208:1240] != bytes(combined[3].pubkey) or credit.uint(data, 1296) != remaining * (1 << 60)):
            raise ValueError("The partial repayment, remaining debt or collateral did not reconcile")
        return dict(slot=result["context"]["slot"], loan_raw=bill.LOAN, payment_raw=payment, remaining_raw=remaining,
                    vendor_raw=bill.LOAN+bill.SALE, pledge_lamports=bill.PLEDGE + extra_pledge, entry_lamports=spends[0], sponsor_lamports=spends[1] if payment else 0,
                    **({"added_lamports": extra_pledge} if extra_pledge else {}))

    probe = run(40_000_000, 40_000_000)
    slot = probe["slot"]
    return run(stop_backup.buffered(probe["entry_lamports"]), stop_backup.buffered(probe["sponsor_lamports"]))


RECORDS = {
    (PAYMENT, 0): ("midtrip-payment", SAMPLE, "f62d755aded86262eb85a9c90de00b4dce9e8f6a709968fd6bc82de539935656"),
    (DOWNSIDE_PAYMENT, 0): ("downside-payment", DOWNSIDE_SAMPLE, "5a7babb5576323b9723ccfe3e96d9973807921a72c4a6b24f2f4b17c173ba93d"),
    (77_600, 2_500_000): ("pledge-and-cash", credit.DATA / "pledge_and_cash.json.gz", "4cf6e66a6b5dcb2888928c448e7462f1e1220eacc99a9139a64f556185979171"),
    (0, 5_391_000): ("pledge-only", credit.DATA / "pledge_only.json.gz", "e0b06b134f2dd8f5dedcfc3c2b8a3b4fdbc8400e553541fd3e450913fdaee50f"),
}
EVIDENCE = {"/evidence/" + name: path for name, path, _ in RECORDS.values()}


@cache
def saved(payment=PAYMENT, extra_pledge=0):
    record = RECORDS.get((payment, extra_pledge))
    if record is None:
        raise ValueError("There is no saved simulation for this amount")
    _, path, digest = record
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("The recorded partial repayment changed")
    return atomic_fuel.replay_calls(json.loads(gzip.decompress(raw)), lambda rpc: simulate(rpc, payment=payment, extra_pledge=extra_pledge))


def matches_opening(row):
    return (row["pledge_lamports"] == bill.PLEDGE * SCALE and row["draw_cents"] * SCALE == bill.LOAN
            and row["sale_cents"] * SCALE == bill.SALE)


def matching_payment(row, decision):
    cents = decision.get("payment_cents", 0)
    return cents * SCALE if matches_opening(row) and 0 < cents * SCALE < bill.LOAN else None


def selected_payment(data):
    price = integer(data.get("price_cents"), "day-15 SOL price", 10_000_000)
    budget = integer(data.get("budget_cents"), "day-15 cash", 100_000_000)
    drop = integer(data.get("drop_bps"), "additional price fall", 9999)
    expected = integer(data.get("payment_cents"), "quoted early payment", bill.LOAN // SCALE)
    if not price or not drop:
        raise ValueError("Choose a price and further fall before verifying this decision")
    terms = mainnet_loan.saved()
    marks, count = midtrip.checkpoints(collateral_path.saved(), price, 2 * terms["apr"])
    row = dict(draw_cents=bill.LOAN // SCALE, sale_cents=bill.SALE // SCALE, pledge_lamports=bill.PLEDGE * SCALE)
    decision = midtrip.quote(row, budget, marks, count, terms, drop_bps=drop)
    payment = matching_payment(row, decision)
    if payment is None or payment != expected * SCALE:
        raise ValueError("The selected amount does not match this loan’s current modeled partial payment")
    return payment
