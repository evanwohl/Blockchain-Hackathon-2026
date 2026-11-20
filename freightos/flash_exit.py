"""Replay a Kamino payoff funded by selling a capped portion of its released SOL."""
import base64
import gzip
import hashlib
import json
import struct
from functools import cache

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV1, TransactionConfig
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from freightos import atomic_fuel, devnet, freight_credit as credit, fuel_swap, load_credit, public_offer, repeat_credit, split_payment as bill, stop_backup

SAMPLE = credit.DATA / "flash_exit.json.gz"
MIXED_SAMPLE = credit.DATA / "cash_exit.json.gz"
SAMPLE_CASH = 300_000


def flash_instruction(owner, cash, amount, *, borrow_index=None):
    a = credit.route()["ixs"][12]["a"]
    def key(i):
        return Pubkey.from_string(a[i][0])
    keys = [AccountMeta(owner, True, False), AccountMeta(key(3), False, False), AccountMeta(key(2), False, False),
            AccountMeta(key(4), False, True), AccountMeta(devnet.MINT, False, False), AccountMeta(key(6), False, True),
            AccountMeta(cash, False, True), AccountMeta(key(7), False, True), AccountMeta(credit.KLEND, False, False),
            AccountMeta(credit.KLEND, False, False), AccountMeta(key(11), False, False), AccountMeta(devnet.TOKEN, False, False)]
    name = "flash_borrow_reserve_liquidity" if borrow_index is None else "flash_repay_reserve_liquidity"
    data = hashlib.sha256(("global:" + name).encode()).digest()[:8] + struct.pack("<Q", amount)
    if borrow_index is not None:
        data += bytes([borrow_index])
    return Instruction(credit.KLEND, data, keys)


def simulate(rpc=devnet.request, *, cash_raw=0):
    if type(cash_raw) is not int or not 0 <= cash_raw < bill.LOAN:
        raise ValueError("Choose a cash contribution below the sample fuel principal")
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("The collateral exit requires Solana Devnet")
    owner, obligation, _, wrapped, cash = repeat_credit.accounts()
    route, slot = fuel_swap.pool_accounts(bill.POOL, 0, rpc)
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    block = rpc("getLatestBlockhash", [{"commitment": "confirmed", "minContextSlot": slot}])
    slot = block["context"]["slot"]
    sponsor_cash = load_credit.borrower_accounts(credit.OWNER)[4]
    bridge = bill.LOAN + 1 - cash_raw
    close = load_credit.instructions(owner, bill.PLEDGE, bill.LOAN, repay=True)

    def run(entry_cap, exit_cap, output, *, sponsor_cap=0, decline=False):
        ops = bill.opening_instructions(owner, cash, route, entry_cap, rent)
        if cash_raw:
            ops.extend(fuel_swap.swap_instructions(credit.OWNER, sponsor_cash, route, cash_raw, sponsor_cap, rent))
            ops.append(public_offer.transfer(sponsor_cash, credit.OWNER, cash, cash_raw))
        borrow_index = len(ops)
        ops.extend([flash_instruction(owner, cash, bridge), *close])
        ops.extend(fuel_swap.swap_instructions(owner, cash, route, output, exit_cap, rent))
        ops.append(flash_instruction(owner, cash, bridge, borrow_index=borrow_index))
        config = TransactionConfig(compute_unit_limit=1200000, loaded_accounts_data_size_limit=16*1024*1024, heap_size=32768)
        msg = MessageV1.try_compile(credit.OWNER, ops, Hash.from_string(block["value"]["blockhash"]), config)
        wire = bytes(VersionedTransaction.populate(msg, [Signature.default()] * msg.header.num_required_signatures))
        if len(wire) > 4096:
            raise ValueError("The collateral exit exceeds the Devnet message limit")
        result = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {
            "commitment": "confirmed", "minContextSlot": slot, "encoding": "base64", "sigVerify": False,
            "innerInstructions": True, "accounts": {"encoding": "base64", "addresses": [str(obligation)]}}])
        v, keys = result["value"], list(msg.account_keys)
        if result["context"]["slot"] < slot:
            raise ValueError("The exit simulation predates its quote")
        swaps = [i for i, ix in enumerate(ops) if ix.program_id == fuel_swap.ORCA]
        if decline:
            balances = v["preBalances"].copy()
            balances[keys.index(credit.OWNER)] -= v["fee"]
            if (v["err"] != {"InstructionError": [swaps[-1], {"Custom": 6037}]} or v["postBalances"] != balances
                    or v["postTokenBalances"] != v["preTokenBalances"]):
                raise ValueError("The tight exit sale limit did not roll back the simulated bundle")
            return dict(slot=result["context"]["slot"])
        if v["err"] is not None:
            raise ValueError("Public Devnet could not fill the collateral-funded payoff")
        groups = {g["index"]: g["instructions"] for g in v["innerInstructions"]}
        def transfers(idx, kind):
            return [ix["parsed"]["info"] for ix in groups[idx] if ix["programId"] == str(devnet.TOKEN)
                    and ix.get("parsed", {}).get("type") == kind]
        def checked(idx):
            return [(t["source"], t["destination"], t["mint"], int(t["tokenAmount"]["amount"]))
                    for t in transfers(idx, "transferChecked")]
        def swap(idx, expected, payer=owner, destination=cash):
            legs = transfers(idx, "transfer")
            scratch = Pubkey.create_with_seed(payer, fuel_swap.SEED, devnet.TOKEN)
            if (len(legs) != 2 or legs[0]["source"] != str(scratch) or legs[0]["destination"] != str(route[1])
                    or legs[1]["source"] != str(route[2]) or legs[1]["destination"] != str(destination)
                    or int(legs[1]["amount"]) != expected):
                raise ValueError("Unexpected Orca sale proceeds")
            return int(legs[0]["amount"])

        entry, sold = swap(swaps[0], bill.SALE), swap(swaps[-1], output)
        sponsor_spent = 0
        if cash_raw:
            sponsor_spent = swap(swaps[1], cash_raw, credit.OWNER, sponsor_cash)
            if not 0 < sponsor_spent <= sponsor_cap or devnet.token_delta({"meta": v}, keys, sponsor_cash, devnet.MINT, credit.OWNER):
                raise ValueError("The sponsored cash contribution did not reconcile")
        debt = credit.route()["ixs"][12]["a"][6][0]
        custody = credit.route()["ixs"][8]["a"][6][0]
        repay_idx, = [i for i, ix in enumerate(ops) if ix.program_id == credit.KLEND
                      and ix.data[:8] == bytes.fromhex("0236980394606dda")]
        deposit_idx, = [i for i, ix in enumerate(ops) if ix.program_id == credit.KLEND
                        and ix.data[:8] == bytes.fromhex("d8e0bf1bcc9766af")]
        original_idx, = [i for i, ix in enumerate(ops) if ix.program_id == credit.KLEND
                         and ix.data[:8] == bytes.fromhex("a1808ff5abc7c206")]
        loan_repay, returned = checked(repay_idx)
        repaid = loan_repay[3]
        if (checked(deposit_idx) != [(str(wrapped), custody, str(credit.SOL), bill.PLEDGE)]
                or checked(original_idx) != [(debt, str(cash), str(devnet.MINT), bill.LOAN)]
                or checked(borrow_index) != [(debt, str(cash), str(devnet.MINT), bridge)]
                or checked(len(ops)-1) != [(str(cash), debt, str(devnet.MINT), bridge)]
                or loan_repay[:3] != (str(cash), debt, str(devnet.MINT)) or not bill.LOAN <= repaid <= bridge + cash_raw
                or returned != (custody, str(wrapped), str(credit.SOL), bill.PLEDGE)):
            raise ValueError("The temporary USDC, original payoff or released collateral differs from the route")
        def change(k):
            idx = keys.index(k)
            return v["postBalances"][idx] - v["preBalances"][idx]
        start, finish = devnet.token_balances(v, keys, cash, devnet.MINT, owner, allow_missing=True)
        vendor_owner = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])
        vendor = devnet.token_delta({"meta": v}, keys, load_credit.vendor(), devnet.MINT, vendor_owner)
        closed, = v["accounts"]
        if (start or finish != output+cash_raw-repaid or not 0 < sold <= exit_cap <= bill.PLEDGE or not 0 < entry <= entry_cap
                or (closed and closed["lamports"]) or v["preBalances"][keys.index(obligation)] or change(obligation)
                or change(owner) != bill.SPONSOR-entry-sold or vendor != bill.LOAN+bill.SALE):
            raise ValueError("Borrower cash, SOL or the closed obligation did not reconcile")
        return dict(sponsor_spent_lamports=sponsor_spent, cash_contributed_raw=cash_raw, slot=result["context"]["slot"], repaid_raw=repaid, entry_lamports=entry, sold_lamports=sold,
                    retained_lamports=bill.PLEDGE-sold, vendor_raw=vendor, bridge_raw=bridge, borrower_cash_raw=start,
                    remaining_usdc_raw=finish, pledge_lamports=bill.PLEDGE, sale_raw=output, message_bytes=len(wire))

    probe = run(40_000_000, bill.PLEDGE, bridge, sponsor_cap=40_000_000)
    slot = probe["slot"]
    cap = min(bill.PLEDGE, stop_backup.buffered(probe["sold_lamports"]))
    entry_cap = stop_backup.buffered(probe["entry_lamports"])
    sponsor_cap = stop_backup.buffered(probe["sponsor_spent_lamports"])
    result = run(entry_cap, cap, probe["repaid_raw"]-cash_raw, sponsor_cap=sponsor_cap)
    slot = result["slot"]
    rejected = run(entry_cap, 1, result["sale_raw"], sponsor_cap=sponsor_cap, decline=True)
    return {**result, "exit_cap_lamports": cap, "rollback_slot": rejected["slot"]}


@cache
def saved():
    raw = SAMPLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "7beb6b176fabb0ef4fa9035b66eba5292ff524b4a23239f63d9dc8b169e1452c":
        raise ValueError("The recorded collateral exit changed")
    return atomic_fuel.replay_calls(json.loads(gzip.decompress(raw)), simulate)


@cache
def saved_mixed():
    raw = MIXED_SAMPLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "569f354b7da3c60243bbaa03e1bc9b16860f72df9627d02abf4adaab5b3a8944":
        raise ValueError("The recorded cash and collateral payoff changed")
    return atomic_fuel.replay_calls(json.loads(gzip.decompress(raw)), lambda rpc: simulate(rpc, cash_raw=SAMPLE_CASH))
