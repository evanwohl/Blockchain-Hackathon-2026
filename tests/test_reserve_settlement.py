import pytest
from solders.transaction_metadata import FailedTransactionMetadata

from freightos.app import LOAD, LoadSettlement
from freightos.reserve import payment_instructions


def test_signed_atomic_settlement_and_idempotent_approval():
    app = LoadSettlement()
    deposit = app.act("deposit", {})
    assert deposit == app.act("deposit", {}) and deposit["wallet_usdc_raw"] == 0
    old = app.act("review", {"scenario": "fuel budget"})
    assert old["usdc_raw"] == 510_000_000
    quote = app.act("review", {"scenario": "oil stress"})
    with pytest.raises(ValueError, match="Review"):
        app.act("approve", {"approval": old["id"]})
    receipt = app.act("approve", {"approval": quote["id"]})
    assert receipt == app.act("approve", {"approval": quote["id"]})
    assert receipt["vendor_received_usdc_raw"] == 555_000_000
    assert 0 < receipt["vault_shares_burned_raw"] <= quote["max_shares_raw"]
    assert (
        receipt["remaining_vault_shares_raw"] + receipt["vault_shares_burned_raw"]
        == deposit["reserve_shares_raw"]
    )
    with pytest.raises(ValueError, match="already settled"):
        app.act("review", {"scenario": "oil stress"})


def test_short_redemption_rolls_back_on_chain():
    app = LoadSettlement()
    app.act("deposit", {})
    fork = app.fork
    quote = app.act("review", {"scenario": "oil stress"})
    before = fork.balance(fork.shares)
    staging, ixs = payment_instructions(
        fork.route,
        fork.owner,
        fork.vendor,
        LOAD,
        quote["usdc_raw"],
        quote["max_shares_raw"] - 1,
        fork.rent,
    )
    failed = fork.vm.send_transaction(fork.tx(ixs))
    assert isinstance(failed, FailedTransactionMetadata)
    assert "insufficient funds" in " ".join(failed.meta().logs()).lower()
    assert fork.balance(fork.shares) == before and fork.balance(fork.vendor) == 0
    assert fork.vm.get_account(staging) is None


def test_duplicate_load_rejected_by_programs_even_with_a_new_signature():
    app = LoadSettlement()
    app.act("deposit", {})
    fork = app.fork
    quote = fork.review(LOAD, 10_000_000)
    fork.approve(quote["id"])
    before = fork.balance(fork.shares)
    fork.vm.expire_blockhash()
    _, ixs = payment_instructions(
        fork.route,
        fork.owner,
        fork.vendor,
        LOAD,
        quote["usdc_raw"],
        quote["max_shares_raw"],
        fork.rent,
    )
    failed = fork.vm.send_transaction(fork.tx(ixs))
    assert isinstance(failed, FailedTransactionMetadata)
    assert (
        fork.balance(fork.shares) == before and fork.balance(fork.vendor) == 10_000_000
    )


def test_wallet_handoff_is_unsigned_and_executes_the_reviewed_payment_on_fork():
    import base64

    from solders.keypair import Keypair
    from solders.transaction import Transaction

    from freightos.live import prepare
    from freightos.reserve import USDC

    app = LoadSettlement()
    fork = app.fork
    app.act("deposit", {})
    payee = Keypair().pubkey()
    destination = fork.token_fixture(payee, USDC, 0)

    def encode(account):
        if account is None:
            return None
        return {
            "owner": str(account.owner),
            "data": [base64.b64encode(account.data).decode(), "base64"],
        }

    def request(method, params):
        from solders.pubkey import Pubkey

        if method == "getGenesisHash":
            return "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
        if method == "getMinimumBalanceForRentExemption":
            return fork.rent
        if method == "getMultipleAccounts":
            return {
                "context": {"slot": fork.slot},
                "value": [
                    encode(fork.vm.get_account(Pubkey.from_string(k)))
                    for k in params[0]
                ],
            }
        if method == "getLatestBlockhash":
            return {
                "value": {
                    "blockhash": str(fork.vm.latest_blockhash()),
                    "lastValidBlockHeight": 100,
                }
            }
        if method == "getFeeForMessage":
            return {"value": 5000}
        assert method == "simulateTransaction" and params[1]["sigVerify"] is False
        tx = Transaction.from_bytes(base64.b64decode(params[0]))
        tx.sign([fork.signer], fork.vm.latest_blockhash())
        run = fork.vm.simulate_transaction(tx)
        after = dict(run.post_accounts())
        return {
            "context": {"slot": fork.slot},
            "value": {
                "err": None,
                "accounts": [
                    encode(after[Pubkey.from_string(k)])
                    for k in params[1]["accounts"]["addresses"]
                ],
            },
        }

    quote = prepare(
        fork.owner, payee, "external wallet", 555_000_000, 520_000_000, request=request
    )
    tx = Transaction.from_bytes(base64.b64decode(quote["transaction_base64"]))
    assert not any(bytes(tx.signatures[0])) and fork.balance(destination) == 0
    tx.sign([fork.signer], fork.vm.latest_blockhash())
    fork.send(tx)
    assert fork.balance(destination) == 555_000_000


def test_guard_rejects_wrong_vendor_then_replays_genuine_receipt():
    from freightos.verify_receipt import replay

    app = LoadSettlement()
    app.act("deposit", {})
    quote = app.act("review", {"scenario": "oil stress"})
    result = app.act("check-vendor", {"approval": quote["id"]})
    assert result["status"] == "rejected" and not result["load_account_created"]
    assert result["other_vendor_usdc_raw"] == 0
    app.act("cancel", {"approval": quote["id"]})
    with pytest.raises(ValueError, match="Review"):
        app.act("approve", {"approval": quote["id"]})
    quote = app.act("review", {"scenario": "oil stress", "profile": "credit line"})
    receipt = app.act("approve", {"approval": quote["id"]})
    assert replay(receipt)["vendor_received_usdc_raw"] == quote["usdc_raw"]
    with pytest.raises(ValueError, match="Bill terms"):
        replay({**receipt, "decision": {**receipt["decision"], "cost_cents": 0}})
    with pytest.raises(ValueError, match="Receipt differs"):
        replay({**receipt, "vendor_received_usdc_raw": 1})
    assert receipt["decision"]["savings_cents"] < 300


def test_cash_available_routes_away_from_defi():
    app = LoadSettlement()
    plan = app.plans["cash on hand"]["oil stress"]
    assert plan["cost_cents"] == plan["payment_cents"] == 0
    with pytest.raises(ValueError, match="cash covers"):
        app.act("review", {"profile": "cash on hand", "scenario": "oil stress"})


def test_program_enforces_share_limit_and_keeps_settled_bill_after_staging_closure():
    from solders.instruction import AccountMeta, Instruction
    from solders.pubkey import Pubkey

    from freightos import bill
    from freightos.reserve import TOKEN

    app = LoadSettlement()
    fork = app.fork
    app.act("deposit", {})
    quote = app.act("review", {"scenario": "oil stress"})
    amount, cap = quote["usdc_raw"], quote["max_shares_raw"]
    key = Pubkey.from_string(quote["bill_account"])
    digest = bytes.fromhex(quote["decision_sha256"])
    _, instructions = payment_instructions(
        fork.route, fork.owner, fork.vendor, LOAD, amount, cap + 1, fork.rent
    )
    instructions.append(bill.attest(fork.owner, key, digest))
    result = fork.vm.send_transaction(fork.tx(instructions))
    assert isinstance(result, FailedTransactionMetadata)
    assert (
        f"Program {bill.PROGRAM} failed: custom program error: 0x9"
        in result.meta().logs()
    )
    assert fork.balance(fork.vendor) == 0
    receipt = app.act("approve", {"approval": quote["id"]})
    close = Instruction(
        TOKEN,
        bytes([9]),
        [
            AccountMeta(Pubkey.from_string(receipt["load_account"]), False, True),
            AccountMeta(fork.owner, True, True),
            AccountMeta(fork.owner, True, False),
        ],
    )
    fork.send(fork.tx([close]))
    assert not fork.vm.get_account(Pubkey.from_string(receipt["load_account"]))
    fork.vm.expire_blockhash()
    result = fork.vm.send_transaction(
        fork.tx(
            bill.authorize(fork.owner, fork.vendor, LOAD, amount, cap, digest, None)
        )
    )
    assert isinstance(result, FailedTransactionMetadata)
    assert (
        f"Program {bill.PROGRAM} failed: custom program error: 0x3"
        in result.meta().logs()
    )
    assert fork.vm.get_account(key).data[0] == 2


def test_refresh_restores_active_review_and_drops_expired_review(monkeypatch):
    import freightos.fork as fork_module

    app = LoadSettlement()
    app.act("deposit", {})
    quote = app.act("review", {"scenario": "fuel budget", "profile": "credit line"})
    current = app.state()["review"]
    assert current["id"] == quote["id"]
    assert current["decision"]["profile"] == "credit line"
    assert 0 < current["expires_in_seconds"] <= 60
    deadline = fork_module.monotonic() + 61
    monkeypatch.setattr(fork_module, "monotonic", lambda: deadline)
    assert app.state()["review"] is None
    with pytest.raises(ValueError, match="expired"):
        app.act("approve", {"approval": quote["id"]})
    assert app.state()["receipt"] is None


def test_outside_payment_is_separate_from_reserve_receipt():
    app = LoadSettlement()
    request = {"profile": "quick pay", "scenario": "oil stress", "version": app.record_version}
    with pytest.raises(ValueError, match="reserve payment first"):
        app.act("record-payment", request)
    app.act("deposit", {})
    review = app.act("review", request)
    with pytest.raises(ValueError, match="current bill"):
        app.act("record-payment", {**request, "profile": "cash on hand"})
    receipt = app.act("approve", {"approval": review["id"]})
    with pytest.raises(ValueError, match="plan on the reserve receipt"):
        app.act("record-payment", {**request, "scenario": "fuel budget"})
    entry = app.act("record-payment", request)
    assert entry["cents"] == 19000
    assert app.act("record-payment", request) == entry
    assert app.state()["receipt"] == receipt
    app.act("undo-record", request)
    assert app.state()["manual_payment"] is None
    assert app.state()["receipt"] == receipt
    with pytest.raises(ValueError, match="bill has changed"):
        app.act("record-payment", request)


def test_cash_record_and_reset_without_a_transfer():
    app = LoadSettlement()
    request = {"profile": "cash on hand", "scenario": "fuel budget", "version": app.record_version}
    assert app.act("record-payment", request)["cents"] == 70000
    with pytest.raises(ValueError, match="already covered"):
        app.act("review", {**request, "profile": "credit line"})
    assert app.state()["deposit"] is None and app.state()["receipt"] is None
    app.act("reset", {})
    assert app.state()["manual_payment"] is None
    with pytest.raises(ValueError, match="bill has changed"):
        app.act("record-payment", request)


def test_outside_payment_reference_is_optional_and_bound_to_the_entry():
    app = LoadSettlement()
    request = {"profile": "cash on hand", "scenario": "fuel budget", "version": app.record_version}
    for reference in ("bad\nreference", "x" * 81, 47):
        with pytest.raises(ValueError, match="printable characters"):
            app.act("record-payment", {**request, "reference": reference})
    assert app.state()["manual_payment"] is None
    entry = app.act("record-payment", {**request, "reference": "  ACH-17 "})
    assert entry["reference"] == "ACH-17"
    assert app.act("record-payment", {**request, "reference": "ACH-17"}) == entry
    with pytest.raises(ValueError, match="already recorded"):
        app.act("record-payment", {**request, "reference": "ACH-18"})
