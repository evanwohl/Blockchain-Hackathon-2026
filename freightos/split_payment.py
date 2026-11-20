"""Simulate the booked-route example's loan and sale paying one Devnet vendor."""
import base64
import gzip
import hashlib
import json
from functools import cache

from solders.hash import Hash
from solders.message import MessageV1, TransactionConfig
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from freightos import atomic_fuel, devnet, freight_credit as credit, fuel_swap, load_credit, public_offer, repeat_credit, stop_backup

SAMPLE = credit.DATA / "split_payment.json.gz"
LOAN = 729_800
SALE = 220_200
PLEDGE = 21_920_000
SPONSOR = 100_000_000
POOL = fuel_swap.POOLS[0]


def opening_instructions(owner, cash, route, maximum, rent):
    sponsor, vendor = credit.OWNER, load_credit.vendor()
    setup = [transfer(TransferParams(from_pubkey=sponsor, to_pubkey=owner, lamports=SPONSOR)),
             devnet.create_ata(sponsor, owner, devnet.MINT)]
    tag = hashlib.sha256(b"global:init_user_metadata").digest()[:8]
    loan = load_credit.instructions(owner, PLEDGE, LOAN)
    if loan[-1].accounts[2].pubkey != vendor or loan[-1].program_id != devnet.TOKEN:
        raise ValueError("Unexpected final Kamino vendor transfer")
    setup.extend(ix for ix in loan[:-1] if not (ix.program_id == credit.KLEND and ix.data[:8] == tag))
    return [*setup, *fuel_swap.swap_instructions(owner, cash, route, SALE, maximum, rent),
            public_offer.transfer(cash, owner, vendor, LOAN + SALE)]


def simulate(rpc=devnet.request):
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("The split payment requires Solana Devnet")
    owner, obligation, _, _, cash = repeat_credit.accounts()
    route, slot = fuel_swap.pool_accounts(POOL, 0, rpc)
    conf = {"commitment": "confirmed", "minContextSlot": slot}
    block = rpc("getLatestBlockhash", [conf])["value"]["blockhash"]
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    vendor = load_credit.vendor()
    sponsor = credit.OWNER
    market = Pubkey.from_string(credit.route()["market"])
    authority = Pubkey.find_program_address([b"lma", bytes(market)], credit.KLEND)[0]
    debt_vault = Pubkey.from_string(credit.route()["ixs"][12]["a"][6][0])
    collateral = Pubkey.from_string(credit.route()["ixs"][8]["a"][6][0])
    payee = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])

    def run(maximum, *, decline=False):
        ops = opening_instructions(owner, cash, route, maximum, rent)
        config = TransactionConfig(compute_unit_limit=850000, loaded_accounts_data_size_limit=16*1024*1024, heap_size=32768)
        msg = MessageV1.try_compile(sponsor, ops, Hash.from_string(block), config)
        wire = bytes(VersionedTransaction.populate(msg, [Signature.default()] * msg.header.num_required_signatures))
        if len(wire) > 4096:
            raise ValueError("Split payment exceeds the Devnet v1 message limit")
        result = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {**conf, "encoding": "base64", "sigVerify": False}])
        value = result["value"]
        if result["context"]["slot"] < conf["minContextSlot"]:
            raise ValueError("The split simulation predates its quote")
        if decline:
            failure = (value["err"] or {}).get("InstructionError", [])
            keys = list(msg.account_keys)
            expected = value["preBalances"].copy()
            expected[keys.index(sponsor)] -= value["fee"]
            if (len(failure) != 2 or failure[1] != {"Custom": 6037} or failure[0] != next(i for i, ix in enumerate(ops) if ix.program_id == fuel_swap.ORCA)
                    or "Program " + str(credit.KLEND) + " success" not in value["logs"]
                    or not any(line.startswith("Program " + str(fuel_swap.ORCA) + " failed:") for line in value["logs"])
                    or value["postBalances"] != expected or value["preTokenBalances"] != value["postTokenBalances"]):
                raise ValueError("The declined swap did not roll back the loan and payment")
            return result["context"]["slot"]
        if value["err"] is not None:
            raise ValueError("Devnet could not fill this combined loan and sale")
        keys = list(msg.account_keys)
        checked = {"meta": value}
        def dollars(address, holder):
            return devnet.token_delta(checked, keys, address, devnet.MINT, holder)
        def native_tokens(address, holder):
            return load_credit.token_change(checked, keys, address, credit.SOL, holder, 9)
        spent = native_tokens(route[1], POOL)
        idx = keys.index(obligation)
        if (not 0 < spent <= maximum or dollars(route[2], POOL) != -SALE or dollars(debt_vault, authority) != -LOAN
                or native_tokens(collateral, authority) != PLEDGE or dollars(cash, owner) or dollars(vendor, payee) != LOAN + SALE
                or value["preBalances"][idx] or value["postBalances"][idx] <= 0):
            raise ValueError("Orca, Kamino and the vendor balances did not reconcile")
        return spent, result["context"]["slot"], len(wire)

    spent, slot, _ = run(40_000_000)
    conf = {**conf, "minContextSlot": slot}
    maximum = stop_backup.buffered(spent)
    spent, slot, size = run(maximum)
    conf = {**conf, "minContextSlot": slot}
    rollback_slot = run(1, decline=True)
    return dict(rollback_slot=rollback_slot, loan_raw=LOAN, sale_raw=SALE, vendor_raw=LOAN+SALE, pledge_lamports=PLEDGE, pool=str(POOL),
                spent_lamports=spent, max_lamports=maximum, slot=slot, message_bytes=size)


@cache
def saved():
    raw = SAMPLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "260dcce0b1cc783f2ad1a6f75dd6a5ceb40b445a9b8917e2ec0a654ac7dff088":
        raise ValueError("The recorded split-payment simulation changed")
    return atomic_fuel.replay_calls(json.loads(gzip.decompress(raw)), simulate)
