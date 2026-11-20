"""Construct vendor-directed Devnet KLend loans and full payoffs for a dedicated borrower."""
import base64
import gzip
import json
import struct
from datetime import date, datetime, timezone
from decimal import Decimal

from solders.hash import Hash
from solders.message import Message, MessageV1
from solders.signature import Signature
from solders.transaction import Transaction, VersionedTransaction

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

from freightos import atomic_fuel, credit_exit, devnet, freight_credit as credit
from freightos.load_calendar import project
from freightos.reserve import associated
from hedging.quick_pay import integer


def borrower_accounts(owner):
    market = Pubkey.from_string(credit.route()["market"])
    empty = bytes(Pubkey.default())
    obligation = Pubkey.find_program_address([b"\0", b"\0", bytes(owner), bytes(market), empty, empty], credit.KLEND)[0]
    metadata = Pubkey.find_program_address([b"user_meta", bytes(owner)], credit.KLEND)[0]
    return (owner, obligation, metadata, associated(owner, credit.SOL), associated(owner, devnet.MINT))


def instructions(owner, collateral, advance, *, repay=False, repay_cap=None):
    originals = borrower_accounts(credit.OWNER)
    if str(originals[1]) != credit.route()["obligation"] or str(originals[2]) != credit.route()["ixs"][4]["a"][2][0]:
        raise ValueError("The borrower account derivation differs from the Kamino route")
    keys = dict(zip(originals, borrower_accounts(owner), strict=True))
    opening = credit.loan_instructions(collateral, advance, "vendor")
    if repay:
        refresh = [ix for ix in opening if ix.program_id == credit.KLEND and ix.data == bytes.fromhex("02da8aeb4fc91966")]
        _, close = credit_exit.exit_route()
        if repay_cap is not None:
            last = close[-1]
            close = [*close[:-1], Instruction(last.program_id, last.data[:8] + struct.pack("<Q", repay_cap) + last.data[16:], last.accounts)]
        selected = [devnet.create_ata(credit.OWNER, credit.OWNER, credit.SOL), *refresh[-2:], *close, opening[-2]]
    else:
        selected = opening
    return [Instruction(ix.program_id, ix.data,
                        [AccountMeta(keys.get(a.pubkey, a.pubkey), a.is_signer, a.is_writable) for a in ix.accounts])
            for ix in selected]


def vendor():
    return Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["decision"]["vendor"])


BORROW = "nH6jUmA4ZXQ9oozEppH95n8MDLX4pAd3ypjkq4mUWgxPW718YtDgVgKv4YfVfnNnC34aaQXPyY2W6uJtVqAPQnS"
REPAY = "537xu1c3PF7SVq3BPixSUUpeE1WX3RNwJpHPNpz8TvpMzhnkrud4M4JRdmJAd9uaTo6QZ3dEYCVQY1muwhHitnAb"
SAMPLE = credit.DATA / "settled_credit.json.gz"


def token_change(record, keys, address, mint, authority, decimals):
    index = keys.index(address)
    amounts = []
    for side in ("preTokenBalances", "postTokenBalances"):
        row, = [row for row in record["meta"][side] if row["accountIndex"] == index]
        if (row["mint"] != str(mint) or row["owner"] != str(authority) or row["programId"] != str(devnet.TOKEN)
                or row["uiTokenAmount"]["decimals"] != decimals):
            raise ValueError("Unexpected lending pool token account")
        amounts.append(int(row["uiTokenAmount"]["amount"]))
    return amounts[1] - amounts[0]


def verify(sample, rpc=None):
    if rpc and rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("This credit receipt requires public Devnet")
    owner = Pubkey.from_string(sample["borrower"])
    advance, collateral = sample["advance_raw"], sample["collateral_lamports"]
    _, obligation, metadata, _, cash = borrower_accounts(owner)
    debt_supply = Pubkey.from_string(credit.route()["ixs"][12]["a"][6][0])
    sol_supply = Pubkey.from_string(credit.route()["ixs"][8]["a"][6][0])
    authority = Pubkey.find_program_address([b"lma", bytes(Pubkey.from_string(credit.route()["market"]))], credit.KLEND)[0]
    payee = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])
    legs = []
    for name, signature in (("borrow", BORROW), ("repay", REPAY)):
        tx = rpc("getTransaction", [signature, {"encoding": "base64", "commitment": "finalized",
                 "maxSupportedTransactionVersion": 0}]) if rpc else sample[name]
        keys = devnet.check_transaction(tx, signature, instructions(owner, collateral, advance, repay=name == "repay"), owner)
        meta = tx["meta"]
        account = keys.index(obligation)
        legs.append(dict(slot=tx["slot"], time=tx["blockTime"], fee=meta["fee"],
                         rent_before=meta["preBalances"][account], rent_after=meta["postBalances"][account],
                         supply=devnet.token_delta(tx, keys, debt_supply, devnet.MINT, authority),
                         wallet=devnet.token_delta(tx, keys, cash, devnet.MINT, owner),
                         pledged=token_change(tx, keys, sol_supply, credit.SOL, authority, 9),
                         native=meta["postBalances"][0] - meta["preBalances"][0]))
        if name == "borrow":
            received = devnet.token_delta(tx, keys, vendor(), devnet.MINT, payee)
            i = keys.index(metadata)
            held = meta["postBalances"][i] - meta["preBalances"][i]
    opened, closed = legs
    if (opened["slot"] >= closed["slot"] or opened["rent_before"] or closed["rent_after"]
            or not opened["rent_after"] == closed["rent_before"] > 0 or held <= 0
            or not received == advance == -opened["supply"] or opened["wallet"] != 0
            or not opened["pledged"] == collateral == -closed["pledged"]
            or not -closed["wallet"] == closed["supply"] >= advance
            or closed["native"] != collateral + closed["rent_before"] - closed["fee"]
            or -opened["native"] != collateral + opened["rent_after"] + held + opened["fee"]):
        raise ValueError("Debt, vendor payment, returned SOL or loan closure did not reconcile")
    return {"borrower": str(owner), "vendor": str(vendor()), "vendor_raw": received, "repaid_raw": closed["supply"],
            "interest_raw": closed["supply"] - advance, "collateral_lamports": collateral, "returned_lamports": -closed["pledged"],
            "obligation_rent_lamports": closed["rent_before"], "metadata_rent_lamports": held,
            "network_lamports": opened["fee"] + closed["fee"], "open_slot": opened["slot"], "close_slot": closed["slot"],
            "elapsed_seconds": closed["time"] - opened["time"], "borrow_signature": BORROW, "repay_signature": REPAY}


def settled(rpc=None):
    return verify(json.loads(gzip.decompress(SAMPLE.read_bytes())), rpc)


def load_terms(data):
    if not isinstance(data, dict):
        raise ValueError("Enter the load amounts and dates first.")
    view = project(data)
    gap = view["gap_cents"]
    if not gap or data["opening_cents"] < data["floor_cents"]:
        raise ValueError("Use credit only for a fuel shortfall after protecting the opening road cash.")
    if data["freight_status"] != "expected" or not data["freight_cents"]:
        raise ValueError("Enter the expected freight payment that will repay this loan.")
    days = (date.fromisoformat(data["freight_date"]) - date.fromisoformat(data["fuel_date"])).days
    if not 1 <= days <= 90:
        raise ValueError("Choose an expected freight payment one to 90 days after the fuel stop.")
    balance = next(row["balance_cents"] for row in reversed(view["rows"]) if row["date"] <= data["freight_date"])
    return gap, days, balance + gap - data["floor_cents"]


def simulate(owner, ops, addresses, slot, rpc):
    config = {"commitment": "confirmed", "minContextSlot": slot}
    block = rpc("getLatestBlockhash", [config])["value"]
    msg = Message.new_with_blockhash(ops, owner, Hash.from_string(block["blockhash"]))
    wire = bytes(Transaction.new_unsigned(msg))
    if len(wire) > 1232:
        raise ValueError("The loan transaction exceeds the Solana size limit.")
    encoded = base64.b64encode(wire).decode()
    run = rpc("simulateTransaction", [encoded, {**config, "encoding": "base64", "sigVerify": False,
              "accounts": {"encoding": "base64", "addresses": [str(a) for a in addresses]}}])
    if run["value"]["err"] is not None or run["context"]["slot"] < slot:
        raise ValueError("Kamino could not simulate this transaction. Check available test funds and pool liquidity.")
    return list(msg.account_keys), run, {"transaction": encoded, "last_valid_height": block["lastValidBlockHeight"]}


def prepare(data, address, limit, rpc=devnet.request):
    gap, days, can_repay = load_terms(data)
    integer(limit, "maximum test SOL pledge in lamports", credit.LAMPORTS * 10)
    if not limit:
        raise ValueError("Set a positive test SOL pledge limit.")
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("A public Devnet connection is required.")
    owner = Pubkey.from_string(address) if address else credit.OWNER
    _, obligation, metadata, wrapped, cash = borrower_accounts(owner)
    state = rpc("getMultipleAccounts", [[str(obligation), str(metadata), str(wrapped), str(cash), credit.route()["sol"], credit.route()["usd"]],
                                       {"encoding": "base64", "commitment": "confirmed"}])
    if any(a is not None for a in state["value"][:3]) or state["value"][3] is None:
        raise ValueError("Use a dedicated Devnet wallet with a test USDC account and no prior Kamino position or wrapped SOL balance.")
    slot = state["context"]["slot"]
    advance = gap * 100
    backing, debt = [credit.read_program_account(a, "2bf2ccca1af73b7f", 8624) for a in state["value"][4:]]
    price, stable = credit.uint(backing, 248), credit.uint(debt, 248)
    initial, debt_factor = backing[4872], credit.uint(debt, 5008, 8)
    if not (price > 0 and stable > 0 and 0 < initial < 100 and debt_factor >= 100):
        raise ValueError("Kamino could not price the collateral.")
    seed = Decimal(advance) * credit.LAMPORTS / 10**6 * stable / price * debt_factor / initial
    pledge = min(limit, credit.ceiling(seed * Decimal("1.02")))
    for _ in range(4):
        keys, sim, wire = simulate(owner, instructions(owner, pledge, advance),
                                   [obligation, credit.route()["usd"], credit.route()["sol"]], slot, rpc)
        q = credit.evaluate_deltas({"collateral": pledge, "response": sim, "checked_at": datetime.now(timezone.utc).isoformat()}, 40, days, advance, 10000, "vendor",
                                   keys, owner, obligation, True)
        if q["minimum_lamports"] > limit:
            raise ValueError("The full fuel gap needs more SOL than this pledge limit at a 40% price drop.")
        if q["repay_cents"] > can_repay:
            raise ValueError("The expected cash on payday cannot repay the loan and protect your road cash.")
        if q["minimum_lamports"] == pledge:
            return {"gap_cents": gap, "days": days, "repay_cents": q["repay_cents"], "cost_cents": q["cost_cents"],
                    "advance_raw": advance, "collateral_lamports": pledge, "setup_lamports": q["setup_lamports"],
                    "network_lamports": q["fee_lamports"], "rate_bps": q["rate_bps"], "slot": q["slot"],
                    "liquidation_price_cents": q["liquidation_price_cents"], "vendor": str(vendor()),
                    "address": str(owner), **(wire if address else {})}
        pledge, slot = q["minimum_lamports"], q["slot"]
    raise ValueError("Collateral prices moved during the quote. Check again.")


def opening(signature, owner, rpc):
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("A public Devnet connection is required.")
    if not isinstance(signature, str) or not 64 <= len(signature) <= 88:
        raise ValueError("Enter the original Solana borrowing signature.")
    Signature.from_string(signature)
    record = rpc("getTransaction", [signature, {"encoding":"base64","commitment":"finalized","maxSupportedTransactionVersion":1}])
    if not record:
        raise ValueError("The borrowing transaction has not finalized on Devnet. Check again.")
    tx = VersionedTransaction.from_bytes(base64.b64decode(record["transaction"][0], validate=True))
    if isinstance(tx.message, MessageV1):
        if record["meta"]["err"] is not None or str(tx.signatures[0]) != signature or not all(tx.verify_with_results()):
            raise ValueError("The signed combined payment could not be verified")
        pledge, debt = atomic_fuel.opening_amounts(tx.message, record["meta"], owner)
        return record["slot"], pledge, debt
    amounts = {}
    for ix in tx.message.instructions:
        if tx.message.account_keys[ix.program_id_index] == credit.KLEND and len(ix.data) == 16:
            amounts[ix.data[:8].hex()] = int.from_bytes(ix.data[8:], "little")
    if not {"d8e0bf1bcc9766af", "a1808ff5abc7c206"} <= amounts.keys():
        raise ValueError("This transaction did not open a FreightOS fuel loan.")
    collateral, advance = amounts["d8e0bf1bcc9766af"], amounts["a1808ff5abc7c206"]
    keys = devnet.check_transaction(record, signature, instructions(owner, collateral, advance), owner)
    payee = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])
    if devnet.token_delta(record, keys, vendor(), devnet.MINT, payee) != advance:
        raise ValueError("The fuel vendor did not receive the expected loan proceeds.")
    return record["slot"], collateral, advance


def prepare_payoff(address, signature, rpc=devnet.request):
    owner = Pubkey.from_string(address)
    slot, collateral, advance = opening(signature, owner, rpc)
    _, obligation, _, wrapped, cash = borrower_accounts(owner)
    config = {"encoding":"base64","commitment":"confirmed","minContextSlot":slot}
    state = rpc("getMultipleAccounts", [[str(obligation), str(wrapped)], config])
    debt, prior_wrapped = state["value"]
    if debt is None or debt["lamports"] == 0:
        return {"closed": True, "address": address, "advance_raw": advance}
    if prior_wrapped is not None:
        raise ValueError("Move the wrapped test SOL out of this dedicated loan wallet before checking payoff.")
    chain = credit.read_program_account(debt,"a8ce8d6a584caca7",3344)
    if chain[64:96] != bytes(owner):
        raise ValueError("Loan owner differs from the connected account.")
    keys, sim, _ = simulate(owner, instructions(owner, collateral, advance, repay=True), [obligation], state["context"]["slot"], rpc)
    def payoff(value, keys):
        before, after = devnet.token_balances(value, keys, cash, devnet.MINT, owner)
        closed, = value["accounts"]
        if (not closed or closed["lamports"] != 0 or closed["owner"] != str(Pubkey.default())
                or value["postBalances"][0] - value["preBalances"][0] != collateral + debt["lamports"] - value["fee"]
                or before - after < advance):
            raise ValueError("Repayment or return of collateral could not be verified.")
        return before - after
    cap = payoff(sim["value"], keys) + 1
    keys, checked, wire = simulate(owner, instructions(owner, collateral, advance, repay=True, repay_cap=cap),
                                    [obligation], sim["context"]["slot"], rpc)
    charge = payoff(checked["value"], keys)
    if charge > cap:
        raise ValueError("The payoff exceeds the approved limit.")
    return {"closed": False, "address": address, "repay_raw": charge, "max_repay_raw": cap,
            "returned_lamports": collateral, "network_lamports": checked["value"]["fee"], **wire}


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Verify a settled FreightOS Devnet fuel loan")
    parser.add_argument("receipt", nargs="?", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    rpc = devnet.request if args.live else None
    result = verify(json.loads(args.receipt.read_bytes()), rpc) if args.receipt else settled(rpc)
    print(json.dumps(result, indent=2))
