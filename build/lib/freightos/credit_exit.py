"""Reconcile a wallet loan and immediate payoff against public Kamino simulations."""
import argparse
import base64
import gzip
import json
from decimal import Decimal
from functools import cache
from pathlib import Path

from solders.address_lookup_table_account import AddressLookupTableAccount
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0, to_bytes_versioned
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from freightos import devnet, freight_credit as credit
from freightos.reserve import TOKEN, associated

SNAPSHOT = credit.DATA / "credit_exits.json.gz"


@cache
def exit_route():
    source = json.loads((credit.DATA / "credit_exit_route.json").read_bytes())
    lookup = AddressLookupTableAccount(Pubkey.from_string(source["key"]), [Pubkey.from_string(a) for a in source["addresses"]])
    close = [Instruction(Pubkey.from_string(ix["program"]), base64.b64decode(ix["data"], validate=True),
                         [AccountMeta(Pubkey.from_string(address), signer, writable) for address, signer, writable in ix["accounts"]])
             for ix in source["close"]]
    return lookup, close


def messages(collateral, advance, block):
    opening = credit.loan_instructions(collateral, advance, "wallet")
    reserve_refreshes = [ix for ix in opening if ix.program_id == credit.KLEND and ix.data == bytes.fromhex("02da8aeb4fc91966")]
    if len(reserve_refreshes) != 3:
        raise ValueError("Missing lending reserve refreshes")
    lookup, close = exit_route()
    payoff = [*reserve_refreshes[-2:], *close, opening[-1]]
    blockhash = Hash.from_string(block)
    round_trip = MessageV0.try_compile(credit.OWNER, opening[:-1] + payoff, [lookup], blockhash)
    separate = MessageV0.try_compile(credit.OWNER, [devnet.create_ata(credit.OWNER, credit.OWNER, credit.SOL), *payoff], [], blockhash)
    return round_trip, separate


def capture(collateral, advance, rpc=devnet.request):
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("This loan check requires public Devnet")
    opening = credit.simulate(collateral, rpc, advance=advance, destination="wallet")
    slot = opening["response"]["context"]["slot"]
    config = {"commitment": "confirmed", "minContextSlot": slot}
    block = rpc("getLatestBlockhash", [config])["value"]["blockhash"]
    round_trip, separate = messages(collateral, advance, block)
    wire = bytes(VersionedTransaction.populate(round_trip, [Signature.default()]))
    if len(wire) > 1232:
        raise ValueError("Loan and payoff exceed the Solana transaction limit")
    encoded = base64.b64encode(wire).decode()
    response = rpc("simulateTransaction", [encoded, {**config, "encoding": "base64", "sigVerify": False, "innerInstructions": True,
                   "accounts": {"encoding": "base64", "addresses": [credit.route()["obligation"]]}}])
    fee_message = base64.b64encode(to_bytes_versioned(separate)).decode()
    fee = rpc("getFeeForMessage", [fee_message, {**config, "minContextSlot": response["context"]["slot"]}])
    return {"opening": opening, "block": block, "wire": encoded, "response": response, "fee_message": fee_message, "fee": fee}


def verify(run, collateral, advance, drop, days):
    opening = run["opening"]
    q = credit.evaluate(opening, drop, days, advance, step=1, destination="wallet")
    slot = run["response"]["context"]["slot"]
    fee = run["fee"]
    if (q["collateral_lamports"] != collateral or not q["slot"] <= slot <= fee["context"]["slot"]
            or type(fee["value"]) is not int or fee["value"] < 0):
        raise ValueError("Payoff observations do not match the loan")
    msg, separate = messages(collateral, advance, run["block"])
    expected = bytes(VersionedTransaction.populate(msg, [Signature.default()]))
    if expected != base64.b64decode(run["wire"], validate=True) or to_bytes_versioned(separate) != base64.b64decode(run["fee_message"], validate=True):
        raise ValueError("Payoff or fee message changed")
    value = run["response"]["value"]
    lookup, _ = exit_route()
    table, = msg.address_table_lookups
    loaded = value["loadedAddresses"]
    if (value["err"] is not None or table.account_key != lookup.key or table.writable_indexes or loaded["writable"]
            or loaded["readonly"] != [str(lookup.addresses[i]) for i in table.readonly_indexes]):
        raise ValueError("Unexpected program result or lookup accounts")
    keys = list(msg.account_keys) + [Pubkey.from_string(a) for a in loaded["readonly"]]
    retained, refunded = reconcile_payoff(value, keys, collateral, advance, q["setup_lamports"])
    backing = credit.read_program_account(opening["response"]["value"]["accounts"][2], "2bf2ccca1af73b7f", 8624)
    price = Decimal(credit.uint(backing, 248)) / credit.UNIT * 100 / credit.LAMPORTS
    return {"slot": slot, "advance_raw": advance, "returned_lamports": collateral, "refunded_lamports": refunded,
            "held_lamports": retained, "exit_fee_lamports": fee["value"], "sol_price": q["sol_price"],
            "refund_cents": int(price * refunded), "held_cents": credit.ceiling(price * retained),
            "entry_cash_cents": q["entry_cash_cents"], "cost_cents": q["cost_cents"],
            "net_cash_cost_cents": q["cost_cents"] + credit.ceiling(price * (retained + q["fee_lamports"] + fee["value"]))}



def reconcile_payoff(value, keys, collateral, advance, setup_lamports):
    accounts = credit.route()
    owner = credit.OWNER
    obligation = Pubkey.from_string(accounts["obligation"])
    wallet = associated(owner, devnet.MINT)
    wrapped = associated(owner, credit.SOL)
    groups = {group["index"]: group["instructions"] for group in value["innerInstructions"]}
    def inner(index, program, name):
        return [ix["parsed"]["info"] for ix in groups[index] if ix["programId"] == str(program)
                and ix.get("parsed", {}).get("type") == name]
    def account_rent(index, address):
        creation, = inner(index, Pubkey.default(), "createAccount")
        if creation["newAccount"] != str(address) or creation["owner"] != str(credit.KLEND) or creation["source"] != str(owner):
            raise ValueError("Unexpected source or owner of setup funds")
        return creation["lamports"]
    def transfers(index):
        return [(t["source"], t["destination"], t["mint"], int(t["tokenAmount"]["amount"])) for t in inner(index, TOKEN, "transferChecked")]
    debt_supply = accounts["ixs"][12]["a"][6][0]
    sol_supply = accounts["ixs"][8]["a"][6][0]
    deposit = (str(wrapped), sol_supply, str(credit.SOL), collateral)
    borrow = (debt_supply, str(wallet), str(devnet.MINT), advance)
    def reverse(leg):
        source, dest, mint, amount = leg
        return dest, source, mint, amount
    if transfers(7) != [deposit] or transfers(11) != [borrow] or transfers(15) != [reverse(borrow), reverse(deposit)]:
        raise ValueError("Pledged SOL or USDC does not reconcile through repayment")
    before, after = value["preBalances"], value["postBalances"]
    closed, = value["accounts"]
    metadata = Pubkey.from_string(accounts["ixs"][4]["a"][2][0])
    refunded = account_rent(4, obligation)
    retained = account_rent(3, metadata)
    cash_before, cash_after = devnet.token_balances(value, keys, wallet, devnet.MINT, owner)
    network = value["fee"]
    if (closed["owner"] != str(Pubkey.default()) or closed["lamports"] != 0 or closed["data"] != ["", "base64"]
            or before[keys.index(obligation)] != 0 or after[keys.index(obligation)] != 0
            or before[keys.index(metadata)] != 0 or after[keys.index(metadata)] != retained
            or before[keys.index(wrapped)] != 0 or after[keys.index(wrapped)] != 0
            or cash_before != cash_after or setup_lamports != retained + refunded
            or type(network) is not int or network < 0 or before[keys.index(owner)] - after[keys.index(owner)] != network + retained):
        raise ValueError("Loan closure and wallet balances do not reconcile")
    return retained, refunded


def check(collateral, advance, drop, days, recorded=False):
    if recorded:
        samples = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
        run = samples[f"{collateral}-{advance}"]
    else:
        run = capture(collateral, advance)
    evidence = {"collateral_lamports": collateral, "advance_raw": advance, "drop": drop, "days": days, "run": run}
    return {**replay(evidence), "evidence": evidence}


def replay(evidence):
    credit.terms(evidence["drop"], evidence["days"])
    return verify(evidence["run"], evidence["collateral_lamports"], evidence["advance_raw"], evidence["drop"], evidence["days"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay the downloaded unsigned Kamino payoff check")
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    print(json.dumps(replay(json.loads(args.evidence.read_bytes())), indent=2))
