import argparse
import base64
import hashlib
import json
from pathlib import Path

from solders.pubkey import Pubkey
from solders.transaction import Transaction

from freightos import bill
from freightos.capture import ROOT
from freightos.fork import ReserveFork
from freightos.reserve import TOKEN, load_seed


def replay(receipt):
    if receipt.get("kind") == "fuel-reserve-return-v1":
        from freightos.reserve_return import replay_return

        return replay_return(receipt)
    payer = Pubkey.from_string(receipt["payer"])
    vendor = Pubkey.from_string(receipt["vendor_owner"])
    fork = ReserveFork(payer, vendor)
    fork.vm.with_blockhash_check(False)
    load = receipt["load"]
    account_key = bill.receipt_address(payer, load)
    staging = Pubkey.create_with_seed(payer, load_seed(load), TOKEN)
    history = receipt["signed_transactions"]
    if len(history) < 3:
        raise ValueError("Incomplete payment history")
    signatures = []
    for wire in history:
        tx = Transaction.from_bytes(base64.b64decode(wire, validate=True))
        tx.verify()
        if tx.message.account_keys[0] != payer:
            raise ValueError("Unexpected transaction payer")
        before_vendor = fork.balance(fork.vendor)
        before_shares = fork.balance(fork.shares)
        prior = fork.vm.get_account(account_key)
        metadata = fork.send(tx)
        signatures.append(str(metadata.signature()))
    record = fork.vm.get_account(account_key)
    if not (
        prior
        and prior.data[0] == 1
        and record
        and record.owner == bill.PROGRAM
        and record.data[0] == 2
    ):
        raise ValueError("Expected an authorized bill to become settled")
    decision = receipt["decision"]
    digest = bill.commitment(decision)
    if "selected" in decision:
        # Historical calendars signed integer day keys. JSON exports turn those keys into strings.
        selected = decision["selected"]
        days = selected["balances_cents"]
        if any(str(int(day)) != str(day) for day in days):
            raise ValueError("Invalid calendar day in receipt")
        decision = {**decision, "selected": {**selected, "balances_cents": {int(d): v for d, v in days.items()}}}
        if digest != record.data[113:]:
            digest = bill.commitment(decision)
    amount = fork.balance(fork.vendor) - before_vendor
    burned = before_shares - fork.balance(fork.shares)
    authorized_amount = int.from_bytes(record.data[97:105], "little")
    share_cap = int.from_bytes(record.data[105:113], "little")
    if not (
        digest == record.data[113:]
        and record.data[1:33] == bytes(payer)
        and record.data[33:65] == bytes(fork.vendor)
        and record.data[65:97] == bytes(staging)
        and amount == authorized_amount
        and 0 < burned <= share_cap
    ):
        raise ValueError("Bill terms differ from replayed balances")
    expected = {
        "signature": signatures[-1],
        "approval": hashlib.sha256(bytes(tx)).hexdigest(),
        "program_id": str(bill.PROGRAM),
        "decision_sha256": digest.hex(),
        "bill_account": str(account_key),
        "bill_status": "settled",
        "payee_account": str(fork.vendor),
        "load_account": str(staging),
        "vendor_received_usdc_raw": amount,
        "vault_shares_burned_raw": burned,
        "remaining_vault_shares_raw": fork.balance(fork.shares),
        "load_remainder_usdc_raw": fork.balance(staging),
        "fee_lamports": metadata.fee(),
        "compute_units": metadata.compute_units_consumed(),
        "cluster": "isolated mainnet fork",
    }
    if receipt["authorization_signature"] != signatures[-2] or any(
        receipt.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("Receipt differs from verified execution")
    if "profile" in decision:
        from freightos.funding import DATA, demo_plans, funding_plan

        evidence = json.loads((DATA / "case_report.json").read_bytes())
        for path, checksum in evidence["input_sha256"].items():
            if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != checksum:
                raise ValueError("Funding source snapshot differs")
        sample = json.loads((DATA / "sample_calendar.json").read_bytes())
        if "surcharge" in decision:
            from freightos.surcharge import surcharge_plans

            terms = decision["surcharge"]
            plan = surcharge_plans(terms["cents"], terms["day"], terms["capacity_cents"])[0][decision["scenario"]]
        elif "cash_policy" in decision or "late_freight" in decision:
            policy = decision.get("cash_policy") or decision["late_freight"]
            version = policy.get("version", 1)
            policies = demo_plans() if version == 2 else demo_plans(version)
            plan = policies[0][decision["profile"]][decision["scenario"]]
        else:
            plan = funding_plan(sample, evidence, decision["scenario"], decision["profile"])
        if (
            plan != decision
            or plan["payment_cents"] * 10000 != amount
            or plan["load"] != load
        ):
            raise ValueError("Funding plan differs from replay")
    if "selected" in decision:
        from hedging.quick_pay import price_calendar

        plan = price_calendar(decision["calendar"])
        draw = sum(
            s["net_proceeds_cents"]
            for s in plan["funding"]
            if s["source"] == "Kamino reserve"
        )
        if (
            plan != decision["selected"]
            or decision["load"] != load
            or draw * 10000 != amount
        ):
            raise ValueError("Synthetic funding plan differs from replay")
    return {
        "verified_signatures": len(signatures),
        "wallet_usdc_raw": fork.balance(fork.cash),
        "slot": fork.slot,
        "bill": str(account_key),
        "vendor_received_usdc_raw": amount,
        "vault_shares_burned_raw": burned,
        "bill_status": "settled",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Replay a FreightOS demo receipt on a fresh local fork"
    )
    parser.add_argument("receipt", type=Path)
    obj = json.loads(parser.parse_args().receipt.read_bytes())
    print(json.dumps(replay(obj.get("receipt", obj)), indent=2))


if __name__ == "__main__":
    main()
