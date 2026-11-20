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

from freightos import atomic_fuel, devnet, freight_credit as credit, fuel_swap, load_credit, public_offer, repeat_credit, split_payment as bill, stop_backup

PAYMENT = 106_800
SAMPLE = credit.DATA / "partial_fuel_payment.json.gz"


def simulate(rpc=devnet.request):
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
    repay = Instruction(credit.KLEND, tag + struct.pack("<Q", PAYMENT), accounts)
    vendor_owner = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])

    def run(entry_cap, sponsor_cap):
        ops = bill.opening_instructions(owner, cash, route, entry_cap, rent)
        ops.extend(fuel_swap.swap_instructions(sponsor, sponsor_cash, route, PAYMENT, sponsor_cap, rent))
        ops.extend([public_offer.transfer(sponsor_cash, sponsor, cash, PAYMENT), *close[1:-2], repay])
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
        for i, (payer, dest, output, cap) in zip((i for i, ix in enumerate(ops) if ix.program_id == fuel_swap.ORCA),
                ((owner, cash, bill.SALE, entry_cap), (sponsor, sponsor_cash, PAYMENT, sponsor_cap)), strict=True):
            a, b = transfers(i, "transfer")
            spent = int(a["amount"])
            if (a["source"] != str(Pubkey.create_with_seed(payer, fuel_swap.SEED, devnet.TOKEN))
                    or a["destination"] != str(route[1]) or not 0 < spent <= cap
                    or b["source"] != str(route[2]) or b["destination"] != str(dest) or int(b["amount"]) != output):
                raise ValueError("Orca did not deliver the funding under its sale limit")
            spends.append(spent)
        minted, = [mint for index in moves for mint in transfers(index, "mintTo")]
        leg, = transfers(len(ops)-1, "transferChecked")
        data = credit.read_program_account(v["accounts"][0], "a8ce8d6a584caca7", 3344)
        remaining = bill.LOAN - PAYMENT
        if (leg["source"] != str(cash) or leg["destination"] != str(combined[5].pubkey)
                or leg["mint"] != str(devnet.MINT) or int(leg["tokenAmount"]["amount"]) != PAYMENT
                or token(combined[5].pubkey, authority) != -remaining or token(load_credit.vendor(), vendor_owner) != bill.LOAN + bill.SALE
                or token(cash, owner) or token(sponsor_cash, sponsor)
                or load_credit.token_change(record, keys, combined[17].pubkey, credit.SOL, authority, 9) != bill.PLEDGE
                or v["preBalances"][keys.index(obligation)] or data[64:96] != bytes(owner)
                or data[32:64] != bytes(combined[2].pubkey) or data[96:128] != bytes(combined[13].pubkey)
                or minted["account"] != str(combined[15].pubkey) or minted["mint"] != str(combined[16].pubkey)
                or minted["mintAuthority"] != str(authority) or not 0 < int(minted["amount"]) == credit.uint(data, 128, 8)
                or data[1208:1240] != bytes(combined[3].pubkey) or credit.uint(data, 1296) != remaining * (1 << 60)):
            raise ValueError("The partial repayment, remaining debt or collateral did not reconcile")
        return dict(slot=result["context"]["slot"], loan_raw=bill.LOAN, payment_raw=PAYMENT, remaining_raw=remaining,
                    vendor_raw=bill.LOAN+bill.SALE, pledge_lamports=bill.PLEDGE, entry_lamports=spends[0], sponsor_lamports=spends[1])

    probe = run(40_000_000, 40_000_000)
    slot = probe["slot"]
    return run(stop_backup.buffered(probe["entry_lamports"]), stop_backup.buffered(probe["sponsor_lamports"]))


@cache
def saved():
    raw = SAMPLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "f62d755aded86262eb85a9c90de00b4dce9e8f6a709968fd6bc82de539935656":
        raise ValueError("The recorded partial repayment changed")
    return atomic_fuel.replay_calls(json.loads(gzip.decompress(raw)), simulate)
