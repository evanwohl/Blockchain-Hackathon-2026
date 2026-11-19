import base64
import gzip
import hashlib
import json
from threading import RLock
from time import monotonic

from solders.account import Account
from solders.clock import Clock
from solders.compute_budget import set_compute_unit_limit
from solders.keypair import Keypair
from solders.litesvm import LiteSVM
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.transaction_metadata import FailedTransactionMetadata

from freightos import bill
from freightos.capture import CLOCK, FIXTURE
from freightos.reserve import (
    TOKEN,
    USDC,
    associated,
    payment_instructions,
    vault_instruction,
)


class VaultExecutionError(RuntimeError):
    pass


class ReserveFork:
    def __init__(self, payer=None, vendor_owner=None):
        manifest = json.loads((FIXTURE / "manifest.json").read_bytes())
        raw = (FIXTURE / "accounts.json.gz").read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["sha256"]:
            raise ValueError("Unexpected fork snapshot")
        snapshot = json.loads(gzip.decompress(raw))
        self.vm = LiteSVM().with_mainnet_features()
        self.vm.add_program(
            bill.PROGRAM, (FIXTURE / "freightos_settlement.so").read_bytes()
        )
        self.slot = snapshot["slot"]
        accounts = snapshot["accounts"]
        programs = snapshot["programdata"]
        for name, value in accounts.items():
            if not value or name in programs or name in programs.values():
                continue
            data = base64.b64decode(value["data"][0], validate=True)
            if name == CLOCK:
                self.vm.set_clock(Clock.from_bytes(data))
                continue
            self.vm.set_account(
                Pubkey.from_string(name),
                Account(
                    value["lamports"],
                    data,
                    Pubkey.from_string(value["owner"]),
                    value["executable"],
                    value["rentEpoch"],
                ),
            )
        for program, data in programs.items():
            self.vm.add_program(
                Pubkey.from_string(program),
                base64.b64decode(accounts[data]["data"][0])[45:],
            )
        self.route = snapshot["withdraw_accounts"]
        self.mint = Pubkey.from_string(self.route[8])
        self.signer = Keypair() if payer is None else None
        self.owner = self.signer.pubkey() if payer is None else payer
        self.vm.airdrop(self.owner, 500_000_000)
        self.rent = self.vm.minimum_balance_for_rent_exemption(165)
        self.cash = self.token_fixture(self.owner, USDC, 600_000_000)
        self.shares = self.token_fixture(self.owner, self.mint, 0)
        self.vendor_owner = vendor_owner or Keypair().pubkey()
        self.vendor = self.token_fixture(self.vendor_owner, USDC, 0)
        self.other_vendor = self.token_fixture(Keypair().pubkey(), USDC, 0)
        self.lock = RLock()
        self.receipts = {}
        self.approvals = {}
        self.history = []

    def token_fixture(self, owner, mint, amount):
        address = associated(owner, mint)
        data = bytearray(165)
        data[:32], data[32:64], data[64:72], data[108] = (
            bytes(mint),
            bytes(owner),
            amount.to_bytes(8, "little"),
            1,
        )
        self.vm.set_account(address, Account(self.rent, bytes(data), TOKEN))
        return address

    def balance(self, key):
        account = self.vm.get_account(key)
        return int.from_bytes(account.data[64:72], "little") if account else 0

    def tx(self, instructions):
        return Transaction.new_signed_with_payer(
            instructions, self.owner, [self.signer], self.vm.latest_blockhash()
        )

    def send(self, tx):
        result = self.vm.send_transaction(tx)
        if isinstance(result, FailedTransactionMetadata):
            raise VaultExecutionError(
                str(result.err()) + " " + "\n".join(result.meta().logs()[-8:])
            )
        self.history.append(base64.b64encode(bytes(tx)).decode())
        return result

    def deposit(self, amount=600_000_000):
        with self.lock:
            ix = vault_instruction(
                self.route, self.owner, self.cash, self.shares, amount, deposit=True
            )
            result = self.send(self.tx([set_compute_unit_limit(1_000_000), ix]))
            return {
                "signature": str(result.signature()),
                "reserve_shares_raw": self.balance(self.shares),
                "wallet_usdc_raw": self.balance(self.cash),
            }

    def redemption_value(self, shares):
        ix = vault_instruction(self.route, self.owner, self.cash, self.shares, shares)
        check = self.vm.simulate_transaction(
            self.tx([set_compute_unit_limit(1_000_000), ix])
        )
        if isinstance(check, FailedTransactionMetadata):
            raise VaultExecutionError(
                "The vault could not redeem these shares on the captured state"
            )
        post = dict(check.post_accounts())[self.cash]
        return int.from_bytes(post.data[64:72], "little") - self.balance(self.cash)

    def review(self, load_id, exact_usdc, decision=None):
        with self.lock:
            if load_id in self.receipts:
                raise ValueError("This load payment already settled")
            high = self.balance(self.shares)
            if not high or self.redemption_value(high) < exact_usdc:
                raise ValueError("Available vault proceeds are below the payment")
            low = 0
            while high - low > 1:
                mid = (low + high) // 2
                if self.redemption_value(mid) >= exact_usdc:
                    high = mid
                else:
                    low = mid
            staging, ixs = payment_instructions(
                self.route,
                self.owner,
                self.vendor,
                load_id,
                exact_usdc,
                high,
                self.rent,
            )
            self.vm.expire_blockhash()
            decision = (
                decision
                if decision is not None
                else {"load": load_id, "payment_usdc_raw": exact_usdc}
            )
            digest = bill.commitment(decision)
            receipt_key = bill.receipt_address(self.owner, load_id)
            creation_rent = (
                None
                if self.vm.get_account(receipt_key)
                else self.vm.minimum_balance_for_rent_exemption(bill.RECEIPT_SIZE)
            )
            authorization = self.send(
                self.tx(
                    bill.authorize(
                        self.owner,
                        self.vendor,
                        load_id,
                        exact_usdc,
                        high,
                        digest,
                        creation_rent,
                    )
                )
            )
            ixs.append(bill.attest(self.owner, receipt_key, digest))
            tx = self.tx(ixs)
            if len(bytes(tx)) > 1232:
                raise ValueError("Payment exceeds the Solana packet limit")
            preview = self.vm.simulate_transaction(tx)
            if isinstance(preview, FailedTransactionMetadata):
                raise VaultExecutionError(
                    "Atomic payment simulation failed: " + str(preview.err())
                )
            after = dict(preview.post_accounts())

            def delta(key):
                return int.from_bytes(after[key].data[64:72], "little") - self.balance(
                    key
                )

            if (
                delta(self.vendor) != exact_usdc
                or not 0 < -delta(self.shares) <= high
                or self.balance(staging)
            ):
                raise ValueError("Unexpected simulated balances")
            wire = bytes(tx)
            review = {
                "id": hashlib.sha256(wire).hexdigest(),
                "load": load_id,
                "slot": self.slot,
                "cluster": "isolated mainnet fork",
                "payer": str(self.owner),
                "payee_account": str(self.vendor),
                "authorization_signature": str(authorization.signature()),
                "authorization_rent_lamports": creation_rent or 0,
                "authorization_fee_lamports": authorization.fee(),
                "bill_account": str(receipt_key),
                "program_id": str(bill.PROGRAM),
                "decision_sha256": digest.hex(),
                "decision": decision,
                "usdc_raw": exact_usdc,
                "max_shares_raw": high,
                "remainder_usdc_raw": delta(staging),
                "network_fee_lamports": preview.meta().fee(),
                "account_rent_lamports": self.rent,
                "expires_in_seconds": 60,
                "transaction_base64": base64.b64encode(wire).decode(),
            }
            self.approvals.clear()
            self.approvals[review["id"]] = (monotonic() + 60, tx, staging, review)
            return review

    def pending_review(self):
        with self.lock:
            for expiry, _, _, review in self.approvals.values():
                remaining = expiry - monotonic()
                if remaining > 0:
                    return {**review, "expires_in_seconds": remaining}
            return None

    def cancel(self, approval_id):
        with self.lock:
            if approval_id not in self.approvals:
                raise ValueError("Review the current payment first")
            review = self.approvals[approval_id][3]
            receipt_key = bill.receipt_address(self.owner, review["load"])
            self.send(self.tx([bill.cancel(self.owner, receipt_key)]))
            del self.approvals[approval_id]
            return {"cancelled": True}

    def check_vendor(self, approval_id):
        with self.lock:
            if approval_id not in self.approvals:
                raise ValueError("Review the current payment first")
            expiry, _, staging, review = self.approvals[approval_id]
            if monotonic() > expiry:
                raise ValueError("Payment review expired")
            load = review["load"]
            receipt_key = bill.receipt_address(self.owner, load)
            _, instructions = payment_instructions(
                self.route,
                self.owner,
                self.other_vendor,
                load,
                review["usdc_raw"],
                review["max_shares_raw"],
                self.rent,
            )
            instructions.append(
                bill.attest(
                    self.owner, receipt_key, bytes.fromhex(review["decision_sha256"])
                )
            )
            result = self.vm.simulate_transaction(self.tx(instructions))
            expected = f"Program {bill.PROGRAM} failed: custom program error: 0xa"
            if (
                not isinstance(result, FailedTransactionMetadata)
                or expected not in result.meta().logs()
            ):
                raise VaultExecutionError("The expected vendor check was not observed")
            return {
                "status": "rejected",
                "reason": "Vendor differs from the approved bill.",
                "program_log": expected,
                "shares_raw": self.balance(self.shares),
                "other_vendor_usdc_raw": self.balance(self.other_vendor),
                "load_account_created": self.vm.get_account(staging) is not None,
            }

    def approve(self, approval_id):
        with self.lock:
            for receipt in self.receipts.values():
                if receipt["approval"] == approval_id:
                    return receipt
            if approval_id not in self.approvals:
                raise ValueError("Review the current payment first")
            expiry, tx, staging, review = self.approvals.pop(approval_id)
            if monotonic() > expiry:
                raise ValueError("Payment review expired")
            shares_before, payee_before = (
                self.balance(self.shares),
                self.balance(self.vendor),
            )
            result = self.send(tx)
            record = self.vm.get_account(Pubkey.from_string(review["bill_account"]))
            if (
                record.owner != bill.PROGRAM
                or record.data[0] != 2
                or record.data[113:].hex() != review["decision_sha256"]
            ):
                raise VaultExecutionError("Settlement receipt reconciliation failed")
            receipt = {
                "load": review["load"],
                "approval": approval_id,
                "cluster": review["cluster"],
                "signature": str(result.signature()),
                "vendor_received_usdc_raw": self.balance(self.vendor) - payee_before,
                "vault_shares_burned_raw": shares_before - self.balance(self.shares),
                "remaining_vault_shares_raw": self.balance(self.shares),
                "load_remainder_usdc_raw": self.balance(staging),
                "fee_lamports": result.fee(),
                "compute_units": result.compute_units_consumed(),
                "payee_account": str(self.vendor),
                "load_account": str(staging),
                "program_id": str(bill.PROGRAM),
                "bill_account": review["bill_account"],
                "authorization_signature": review["authorization_signature"],
                "decision_sha256": review["decision_sha256"],
                "decision": review["decision"],
                "bill_status": "settled",
                "payer": str(self.owner),
                "vendor_owner": str(self.vendor_owner),
                "signed_transactions": list(self.history),
            }
            self.receipts[review["load"]] = receipt
            return receipt
