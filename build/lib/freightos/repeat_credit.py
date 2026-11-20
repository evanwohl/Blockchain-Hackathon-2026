"""Replay a returning borrower's vendor payment and immediate payoff on public Devnet."""
import base64
import gzip
import hashlib
import json

from solders.hash import Hash
from solders.message import MessageV1, TransactionConfig
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from freightos import devnet, freight_credit as credit, load_credit, public_offer

SAMPLE = credit.DATA / "repeat_credit.json.gz"
PLEDGE = 285_000
ADVANCE = 5_700
SPONSOR_SOL = 30_000_000
PAYOFF_CASH = ADVANCE + 1


def accounts():
    receipt = load_credit.settled()
    owner = Pubkey.from_string(receipt["borrower"])
    return load_credit.borrower_accounts(owner)


def message(block, close):
    owner, _, _, _, cash = accounts()
    sponsor = credit.OWNER
    ops = [transfer(TransferParams(from_pubkey=sponsor, to_pubkey=owner, lamports=SPONSOR_SOL)),
           devnet.create_ata(sponsor, owner, devnet.MINT)]
    create_profile = hashlib.sha256(b"global:init_user_metadata").digest()[:8]
    ops.extend(ix for ix in load_credit.instructions(owner, PLEDGE, ADVANCE)
               if not (ix.program_id == credit.KLEND and ix.data[:8] == create_profile))
    if close:
        ops.append(public_offer.transfer(load_credit.borrower_accounts(sponsor)[4], sponsor, cash, PAYOFF_CASH))
        ops.extend(load_credit.instructions(owner, PLEDGE, ADVANCE, repay=True))
    config = TransactionConfig(compute_unit_limit=650000, loaded_accounts_data_size_limit=16 * 1024 * 1024, heap_size=32768)
    return MessageV1.try_compile(sponsor, ops, Hash.from_string(block), config)


def capture(rpc=devnet.request):
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("Use public Devnet to simulate a repeat loan")
    owner, obligation, profile, wrapped, cash = accounts()
    watched = [str(k) for k in (owner, obligation, profile, wrapped, cash, load_credit.vendor())]
    state = rpc("getMultipleAccounts", [watched, {"commitment": "confirmed", "encoding": "base64"}])
    conf = {"commitment": "confirmed", "minContextSlot": state["context"]["slot"]}
    block = rpc("getLatestBlockhash", [conf])["value"]["blockhash"]
    sample = dict(genesis=devnet.GENESIS, block=block, accounts=watched, state=state)
    for key in ("open", "close"):
        msg = message(block, key == "close")
        wire = bytes(VersionedTransaction.populate(msg, [Signature.default(), Signature.default()]))
        if len(wire) > 4096:
            raise ValueError("Repeat loan exceeds the Devnet v1 message limit")
        sample[key] = rpc("simulateTransaction", [base64.b64encode(wire).decode(), {
            **conf, "encoding": "base64", "sigVerify": False, "accounts": {"encoding": "base64", "addresses": watched}}])
        conf["minContextSlot"] = sample[key]["context"]["slot"]
    return sample


def verify(sample):
    owner, obligation, profile, wrapped, cash = accounts()
    watched = (owner, obligation, profile, wrapped, cash, load_credit.vendor())
    if sample["genesis"] != devnet.GENESIS or sample["accounts"] != list(map(str, watched)):
        raise ValueError("Unexpected network or repeat borrower")
    state = sample["state"]
    _, debt, metadata, scratch, _, _ = state["value"]
    if debt or scratch or not metadata:
        raise ValueError("The replay needs a closed loan and a retained profile")
    credit.read_program_account(metadata, hashlib.sha256(b"account:UserMetadata").hexdigest()[:16], 1032)
    authority = Pubkey.find_program_address([b"lma", bytes(Pubkey.from_string(credit.route()["market"]))], credit.KLEND)[0]
    supply = Pubkey.from_string(credit.route()["ixs"][12]["a"][6][0])
    collateral = Pubkey.from_string(credit.route()["ixs"][8]["a"][6][0])
    vendor_owner = Pubkey.from_string(json.loads(devnet.SAMPLE.read_bytes())["vendor_owner"])
    slot = state["context"]["slot"]
    for key in ("open", "close"):
        result = sample[key]
        v = result["value"]
        keys = list(message(sample["block"], key == "close").account_keys)
        if result["context"]["slot"] < slot or v["err"] is not None:
            raise ValueError("The repeat-loan simulation failed or used stale state")
        slot = result["context"]["slot"]
        _, loan, after, _, _, _ = v["accounts"]
        def native(address):
            i = keys.index(address)
            return v["postBalances"][i] - v["preBalances"][i]
        def dollars(address, holder):
            return devnet.token_delta({"meta": v}, keys, address, devnet.MINT, holder)
        pledged = load_credit.token_change({"meta": v}, keys, collateral, credit.SOL, authority, 9)
        if (after["owner"] != metadata["owner"] or after["data"] != metadata["data"]
                or after["lamports"] != metadata["lamports"] or native(profile) or native(wrapped)
                or native(credit.OWNER) != -SPONSOR_SOL - native(cash) - v["fee"]
                or dollars(watched[-1], vendor_owner) != ADVANCE or v["preBalances"][keys.index(obligation)]):
            raise ValueError("The reused profile, temporary accounts or vendor payment did not reconcile")
        if key == "open":
            credit.read_program_account(loan, hashlib.sha256(b"account:Obligation").hexdigest()[:16], 3344)
            rent = loan["lamports"]
            if (not rent > 0 or native(obligation) != rent or native(owner) != SPONSOR_SOL - rent - PLEDGE
                    or dollars(supply, authority) != -ADVANCE or dollars(cash, owner) or pledged != PLEDGE):
                raise ValueError("The reopened loan did not reconcile")
        else:
            repaid = ADVANCE + dollars(supply, authority)
            sponsor_cash = load_credit.borrower_accounts(credit.OWNER)[4]
            if ((loan and loan["lamports"]) or native(obligation) or native(owner) != SPONSOR_SOL or pledged
                    or not ADVANCE <= repaid <= PAYOFF_CASH or dollars(sponsor_cash, credit.OWNER) != -PAYOFF_CASH
                    or dollars(cash, owner) != PAYOFF_CASH - repaid):
                raise ValueError("The payoff did not close the reopened loan")
    return dict(borrower=str(owner), profile=str(profile), profile_lamports=metadata["lamports"], returned_rent=rent,
                vendor_raw=ADVANCE, repaid_raw=repaid, collateral_lamports=PLEDGE,
                open_slot=sample["open"]["context"]["slot"], close_slot=slot)


def saved():
    raw = SAMPLE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "ffa2c7af3088fafe162bdf8e0c6692c01171d79b6da095043e49268ac8c23722":
        raise ValueError("The captured repeat-loan simulation changed")
    return verify(json.loads(gzip.decompress(raw)))
