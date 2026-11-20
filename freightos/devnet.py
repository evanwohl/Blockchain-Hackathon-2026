"""Verify or execute the FreightOS public Devnet fuel-payment example."""

import argparse
import base64
import json
import struct
from pathlib import Path
from urllib.request import Request, urlopen

from solders.compute_budget import set_compute_unit_limit
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.transaction import Transaction

from freightos import bill
from freightos.reserve import ATA, TOKEN, associated, load_seed, payment_instructions, vault_instruction

GENESIS = "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG"
PROGRAM = Pubkey.from_string("6UQBiPuy7YZcFBtGZFHTQ39CyViKnA6FYMjEWgYQtrMq")
VAULT_PROGRAM = Pubkey.from_string("devkRngFnfp4gBc5a3LsadgbQKdPo8MSZ4prFiNSVmY")
VAULT = Pubkey.from_string("7uib8xGAwkaPz4ZGCA6t8sSEid5Yp9ty13PHUweTypx")
MINT = Pubkey.from_string("4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU")
SHARES = Pubkey.from_string("hXm2xSRF5PLKGMrTvAWqKhR76MuJX5dAabeSChkjqu2")
VENUE = VAULT_PROGRAM, VAULT, MINT
SAMPLE = Path(__file__).with_name("artifacts") / "devnet" / "sample.json"
PAYMENT_SIGNATURE = "5A5yKCDw8qZAr4MwvAHBm3op1FdS79wPM7kCD8k6czkRqAG2dx8EgxCtRvbyooy9vozHCurhviXxEeMPLhuvoKv3"


def request(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = Request("https://api.devnet.solana.com", body.encode(), {"Content-Type": "application/json"})
    with urlopen(req, timeout=8) as response:
        payload = json.load(response)
    if "error" in payload:
        raise ValueError(f"Devnet {method} failed: {payload['error']}")
    return payload["result"]


def wallet_balances(address, rpc=request):
    """Read the payer and the Circle USDC account used by PublicPayment in one slot."""
    payer = Pubkey.from_string(address)
    account = associated(payer, MINT)
    snapshot = rpc("getMultipleAccounts", [[str(payer), str(account)],
                   {"encoding": "jsonParsed", "commitment": "confirmed"}])
    native, tokens = snapshot["value"]
    amount = 0
    if tokens is not None:
        info = tokens["data"]["parsed"]["info"]
        if (tokens["owner"] != str(TOKEN) or info["owner"] != address or
                info["mint"] != str(MINT) or info["tokenAmount"]["decimals"] != 6):
            raise ValueError("Unexpected Devnet token account")
        amount = int(info["tokenAmount"]["amount"])
    return {"address": address, "slot": snapshot["context"]["slot"],
            "lamports": native["lamports"] if native else 0, "usdc_raw": str(amount)}


def create_ata(payer, owner, mint):
    return Instruction(ATA, b"\x01", [
        AccountMeta(payer, True, True), AccountMeta(associated(owner, mint), False, True),
        AccountMeta(owner, False, False), AccountMeta(mint, False, False),
        AccountMeta(Pubkey.default(), False, False), AccountMeta(TOKEN, False, False),
    ])


class PublicPayment:
    def __init__(self, run, payer):
        decision = run["decision"]
        self.digest = bill.commitment(decision)
        self.payer = payer
        self.payee = Pubkey.from_string(run["vendor_owner"])
        self.destination = associated(self.payee, MINT)
        self.load = decision["load"]
        self.amount = decision["reserve_cents"] * decision["raw_units_per_cent"]
        self.cap = run["share_cap_raw"]
        if (decision["cluster"] != "devnet" or decision["mint"] != str(MINT)
                or decision["vendor"] != str(self.destination) or self.payee == payer
                or decision["reserve_cents"] + decision["outside_cents"] != decision["bill_cents"]):
            raise ValueError("Invalid public payment terms")
        self.receipt = bill.receipt_address(payer, self.load, PROGRAM)
        self.staging = Pubkey.create_with_seed(payer, load_seed(self.load), TOKEN)
        self.cash = associated(payer, MINT)
        self.shares = associated(payer, SHARES)
        self.route = list(map(str, [
            payer, VAULT, "eJp1RgQNVp3peArmzPkoCFy6jsa17D5Lr9psVteCkwb",
            "22HcDmvyNb4qZJ5qHPysRiSBUyK3fxfEXckG4cStquqx", "5KAFjDo1q6aMdiLhLrnxyU5MBkYrtdYWEZSn1uu8Qqux",
            self.cash, MINT, self.shares, SHARES, TOKEN, TOKEN,
            "KLend2g3cP87fffoy8q1mQqGKjrxjC8boSyAYavgmjD", "G3eJyiJtotrExbXL48saWVGoQmoZTZbgE7rCkwbiBYfD",
            VAULT_PROGRAM, "HRwMj8uuoGVWCanKzKvpTWN5ZvXjtjKGxcFbn2qTPKMW", "6aaNTBEmwdN19AAdTwbNrWyUo6iEyiLguxCTePEzSqoH",
        ]))
        self.deposit = [
            set_compute_unit_limit(400000), create_ata(payer, payer, SHARES), create_ata(payer, self.payee, MINT),
            vault_instruction(self.route, payer, self.cash, self.shares, run["deposit_raw"], deposit=True, venue=VENUE),
        ]
        self.approval = bill.authorize(payer, self.destination, self.load, self.amount, self.cap, self.digest,
                                       run["receipt_rent"], program=PROGRAM)
        _, payment = payment_instructions(self.route, payer, self.destination, self.load, self.amount,
                                          self.cap, run["token_rent"], venue=VENUE)
        self.payment = [*payment, bill.attest(payer, self.receipt, self.digest, program=PROGRAM)]

    def check_receipt(self, account, state):
        expected = b"".join([bytes([state]), bytes(self.payer), bytes(self.destination), bytes(self.staging),
                             struct.pack("<QQ", self.amount, self.cap), self.digest])
        if (not account or account["owner"] != str(PROGRAM) or account["executable"]
                or base64.b64decode(account["data"][0], validate=True) != expected):
            raise ValueError("The public receipt does not match the approved bill")


def check_transaction(record, signature, instructions, payer):
    if not record or record["meta"]["err"] is not None or record["transaction"][1] != "base64":
        raise ValueError("Finalized transaction unavailable or unsuccessful")
    tx = Transaction.from_bytes(base64.b64decode(record["transaction"][0], validate=True))
    expected = Message.new_with_blockhash(instructions, payer, tx.message.recent_blockhash)
    if tx.message != expected or str(tx.signatures[0]) != signature or not all(tx.verify_with_results()):
        raise ValueError("The signed instructions differ from this payment")
    return tx.message.account_keys


def token_balances(meta, keys, account, mint, owner, *, allow_missing=False):
    index = keys.index(account)

    def balance(side):
        rows = [row for row in meta[side] if row["accountIndex"] == index]
        if not rows and allow_missing:
            return 0
        if len(rows) != 1:
            raise ValueError("Missing or duplicate token balance")
        row = rows[0]
        if (row["mint"] != str(mint) or row["owner"] != str(owner) or row["programId"] != str(TOKEN)
                or row["uiTokenAmount"]["decimals"] != 6):
            raise ValueError("Unexpected token balance identity")
        return int(row["uiTokenAmount"]["amount"])

    return balance("preTokenBalances"), balance("postTokenBalances")


def token_delta(record, keys, account, mint, owner):
    before, after = token_balances(record["meta"], keys, account, mint, owner, allow_missing=True)
    return after - before


def verify(run, rpc=None):
    payment = PublicPayment(run, Pubkey.from_string(run["payer"]))
    records = run["transactions"]
    if records["payment"]["signature"] != PAYMENT_SIGNATURE:
        raise ValueError("Unexpected sample payment signature")
    if rpc and rpc("getGenesisHash", []) != GENESIS:
        raise ValueError("Expected Solana Devnet")
    slots = []
    signatures = {}
    for step in ("deposit", "approval", "payment"):
        signature = records[step]["signature"]
        record = rpc("getTransaction", [signature, {"encoding": "base64", "commitment": "finalized",
                                                    "maxSupportedTransactionVersion": 0}]) if rpc else records[step]
        keys = check_transaction(record, signature, getattr(payment, step), payment.payer)
        slots.append(record["slot"])
        signatures[step] = signature
        if step == "deposit":
            minted = token_delta(record, keys, payment.shares, SHARES, payment.payer)
            if minted <= 0 or token_delta(record, keys, payment.cash, MINT, payment.payer) != -run["deposit_raw"]:
                raise ValueError("Reserve deposit amount or issued shares differ")
        if step == "payment":
            received = token_delta(record, keys, payment.destination, MINT, payment.payee)
            pre_shares, post_shares = token_balances(record["meta"], keys, payment.shares, SHARES, payment.payer)
            burned = pre_shares - post_shares
            if received != payment.amount or not 0 < burned <= min(payment.cap, minted):
                raise ValueError("Vendor proceeds or redeemed shares differ")
    if slots != sorted(slots):
        raise ValueError("Public payment steps are out of order")
    receipt = rpc("getAccountInfo", [str(payment.receipt), {"encoding": "base64", "commitment": "finalized",
                                                            "minContextSlot": slots[-1]}]) if rpc else run["receipt"]
    payment.check_receipt(receipt["value"], 2)
    return {"load": payment.load, "slot": slots[-1], "vendor_received_raw": received, "shares_burned_raw": burned,
            "decision": run["decision"], "deposit_raw": run["deposit_raw"], "shares_minted_raw": minted, "signatures": signatures,
            "shares_after_raw": post_shares, "receipt": str(payment.receipt), "share_account": str(payment.shares), "source": "public-rpc" if rpc else "saved-receipt"}


def reserve_preview(run, proof, portion, rpc):
    if portion not in ("road", "all") or proof["source"] != "public-rpc":
        raise ValueError("A live public payment check is required")
    plan = PublicPayment(run, Pubkey.from_string(run["payer"]))
    scale = proof["decision"]["raw_units_per_cent"]
    target = 1000 * scale if portion == "road" else None
    return redemption_quote(plan, proof["slot"], proof["shares_minted_raw"] - proof["shares_burned_raw"],
                            scale, portion, target, rpc)


def simulate_instructions(plan, instructions, slot, rpc, *, trace=False):
    message = Message.new_with_blockhash([set_compute_unit_limit(400000), *instructions], plan.payer, Hash.default())
    wire = base64.b64encode(bytes(Transaction.new_unsigned(message))).decode()
    options = {"commitment": "finalized", "minContextSlot": slot, "encoding": "base64",
               "replaceRecentBlockhash": True, "sigVerify": False}
    if trace:
        options["innerInstructions"] = True
    response = rpc("simulateTransaction", [wire, options])
    if response["value"]["err"] is not None or response["context"]["slot"] < slot:
        raise ValueError("Public reserve simulation unavailable")
    return response["context"]["slot"], message.account_keys, response["value"]


def redemption_quote(plan, since_slot, remaining_shares, scale, portion, target, rpc):
    config = {"commitment": "finalized", "minContextSlot": since_slot}
    account = rpc("getAccountInfo", [str(plan.shares), {**config, "encoding": "jsonParsed"}])
    slot = account["context"]["slot"]
    if slot < since_slot:
        raise ValueError("Share balance predates payment")
    shares = 0
    if account["value"] is not None:
        value = account["value"]
        info = value["data"]["parsed"]["info"]
        if (value["owner"] != str(TOKEN) or value["executable"] or value["data"]["parsed"]["type"] != "account"
                or info["owner"] != str(plan.payer) or info["mint"] != str(SHARES) or info["tokenAmount"]["decimals"] != 6):
            raise ValueError("Unexpected public share account")
        shares = int(info["tokenAmount"]["amount"])
    cap = min(shares, remaining_shares)
    result = {"portion": portion, "slot": slot, "share_account": str(plan.shares), "wallet_shares_raw": shares,
              "eligible_shares_raw": cap, "shares_used_raw": 0, "return_raw": 0, "target_raw": target,
              "raw_units_per_cent": scale, "source": "public-rpc"}
    if not cap:
        return result

    full = withdrawal_value(plan, result, cap, rpc)
    return sized_redemption(plan, full, target, rpc) if target is not None else full


def withdrawal_value(plan, budget, units, rpc):
    withdrawal = vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, units, venue=VENUE)
    slot, keys, value = simulate_instructions(plan, [withdrawal], budget["slot"], rpc)
    before, after = token_balances(value, keys, plan.shares, SHARES, plan.payer)
    cash_before, cash_after = token_balances(value, keys, plan.cash, MINT, plan.payer)
    if before != budget["wallet_shares_raw"] or before - after != units or cash_after <= cash_before:
        raise ValueError("Share balance or withdrawal proceeds changed")
    return {**budget, "slot": slot, "shares_used_raw": units, "return_raw": cash_after - cash_before,
            "network_fee_lamports": value["fee"]}


def sized_redemption(plan, full, target, rpc):
    if full["return_raw"] <= target:
        return full
    cap, scale = full["eligible_shares_raw"], full["raw_units_per_cent"]
    quote = full
    low, low_value = 0, 0
    high, high_value = cap, full["return_raw"]
    for _ in range(4):
        if high - low == 1:
            quote = withdrawal_value(plan, quote, high, rpc)
            if target <= quote["return_raw"] < target + scale:
                return quote
            break
        amount = low + ((target - low_value) * (high - low) + high_value - low_value - 1) // (high_value - low_value)
        amount = min(high - 1, max(low + 1, amount))
        quote = withdrawal_value(plan, quote, amount, rpc)
        proceeds = quote["return_raw"]
        if target <= proceeds < target + scale:
            return quote
        if not low_value <= proceeds <= high_value:
            break
        if proceeds < target:
            low, low_value = amount, proceeds
        else:
            high, high_value = amount, proceeds
    raise ValueError("Could not size the public quote at the current vault price")


def execute(run, payer_file, step):
    if request("getGenesisHash", []) != GENESIS:
        raise ValueError("Expected Solana Devnet")
    signer = Keypair.from_json(payer_file.read_text())
    run = {**run, "receipt_rent": request("getMinimumBalanceForRentExemption", [bill.RECEIPT_SIZE]),
           "token_rent": request("getMinimumBalanceForRentExemption", [165])}
    plan = PublicPayment(run, signer.pubkey())
    config = {"encoding": "base64", "commitment": "confirmed"}
    current = request("getAccountInfo", [str(plan.receipt), config])["value"]
    if step == "approve":
        if current:
            raise ValueError("This wallet already has a public approval or payment for this load")
        instructions = [*plan.deposit, *plan.approval]
    else:
        plan.check_receipt(current, 1)
        instructions = plan.payment
    block = request("getLatestBlockhash", [{"commitment": "confirmed"}])["value"]
    tx = Transaction.new_signed_with_payer(instructions, signer.pubkey(), [signer], Hash.from_string(block["blockhash"]))
    wire = base64.b64encode(bytes(tx)).decode()
    result = request("simulateTransaction", [wire, {**config, "sigVerify": True}])["value"]
    if result["err"] is not None:
        raise ValueError(f"Devnet preflight failed: {result['err']}")
    print(f"Signed {step} transaction {tx.signatures[0]}", flush=True)
    signature = request("sendTransaction", [wire, {"encoding": "base64", "preflightCommitment": "confirmed"}])
    if signature != str(tx.signatures[0]):
        raise ValueError("Unexpected submitted signature")
    return {"status": "submitted", "explorer": f"https://explorer.solana.com/tx/{signature}?cluster=devnet"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "quote", "approve", "pay"], default="verify", nargs="?")
    parser.add_argument("--payer-file", type=Path, help="Dedicated Devnet demo keypair")
    parser.add_argument("--portion", choices=["road", "all"], default="road", help="Public reserve preview")
    args = parser.parse_args()
    run = json.loads(SAMPLE.read_bytes())
    if args.action in ("approve", "pay") and not args.payer_file:
        parser.error("Public execution requires --payer-file")
    if args.action in ("verify", "quote"):
        result = verify(run, request)
        if args.action == "quote":
            result = reserve_preview(run, result, args.portion, request)
    else:
        result = execute(run, args.payer_file, args.action)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
