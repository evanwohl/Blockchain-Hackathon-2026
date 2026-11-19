from concurrent.futures import ThreadPoolExecutor

import pytest

from freightos import reserve_return
from freightos.app import LoadSettlement, trial_data, verified_sample
from freightos.verify_receipt import replay


def quote(app, profile="quick pay"):
    body = {"version": app.record_version, "profile": profile}
    return {**body, "id": app.act("return-quote", body)["id"]}


def test_return_surplus_before_payment_protects_every_fuel_budget():
    app = LoadSettlement()
    app.act("deposit", {})
    data = quote(app, "road cash")
    q = app.act("return-quote", data)
    assert q["remaining_value_raw"] >= q["protected_cents"] * 10000 == 464_000_000
    next_release = reserve_return.withdraw(app.fork, q["return_shares_raw"] + 1)
    left = reserve_return.withdraw(app.fork, q["shares_before_raw"] - q["return_shares_raw"] - 1)
    assert (reserve_return.simulated_cash(app.fork, [next_release, left])
            - reserve_return.simulated_cash(app.fork, [next_release])) < 464_000_000
    review = app.act("review", {"profile": "road cash", "scenario": "oil stress"})
    with pytest.raises(ValueError, match="pending"):
        app.act("return-reserve", data)
    app.act("cancel", {"approval": review["id"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.act("return-reserve", data), range(2)))
    assert results[0] == results[1]
    proof = replay(results[0])
    assert proof["returned_usdc_raw"] == 135_997_990
    assert proof["protected_value_usdc_raw"] == 464_000_000
    assert app.state()["setup_cents"]["road cash"] == 0
    review = app.act("review", {"profile": "road cash", "scenario": "wti-93"})
    paid = app.act("approve", {"approval": review["id"]})
    assert replay(paid)["vendor_received_usdc_raw"] == 464_000_000


def test_return_unused_money_after_vendor_payment_and_receipt_replay():
    app = LoadSettlement()
    app.act("deposit", {"profile": "quick pay", "version": app.record_version, "max_cents": 57001})
    stale = quote(app, "cash on hand")
    review = app.act("review", {"profile": "quick pay", "scenario": "fuel budget"})
    paid = app.act("approve", {"approval": review["id"]})
    with pytest.raises(ValueError, match="on this bill"):
        app.act("return-reserve", stale)
    data = quote(app)
    returned = app.act("return-reserve", data)
    result = replay(returned)
    assert result["wallet_usdc_raw"] == 89_997_991 and result["returned_usdc_raw"] == 60_007_991
    assert not app.state()["reserve_shares_raw"]
    assert replay(paid)["wallet_usdc_raw"] == 29_990_000
    assert app.act("verify-return", {"signature": returned["signature"]}) == result
    assert app.act("return-receipt", {"signature": returned["signature"]}) == returned
    with pytest.raises(ValueError):
        replay({**returned, "quote": {**returned["quote"], "protected_cents": 1}})
    with pytest.raises(ValueError):
        replay({**returned, "wallet_after_raw": 0})
    assert app.act("record-payment", {"scenario": "fuel budget", "profile": "quick pay",
                                     "version": app.record_version})["cents"] == 19000
    app.act("reset", {"version": app.record_version})
    assert app.state()["last_return"] is None
    with pytest.raises(ValueError, match="changed"):
        app.act("return-reserve", data)


def test_quotes_fail_independently_and_tiny_balances_remain_explicit(monkeypatch):
    from freightos.fork import VaultExecutionError

    app = LoadSettlement()
    app.act("deposit", {"profile": "quick pay", "version": app.record_version, "max_cents": 57001})
    proposed = app.act("return-quote", {"profile": "quick pay", "version": app.record_version})
    assert proposed["return_usdc_raw"] == 0
    with pytest.raises(ValueError, match="Review"):
        app.act("return-reserve", proposed)
    normal = app.fork.deposit_quote
    def constrained(target):
        if target == 59900:
            raise VaultExecutionError("Insufficient proceeds")
        return normal(target)
    monkeypatch.setattr(app.fork, "deposit_quote", constrained)
    app.quote_balances = None
    state = app.state()
    assert state["setup_cents"]["credit line"] is None
    assert state["setup_cents"]["road cash"] == 0
    review = app.act("review", {"profile": "quick pay", "scenario": "wti-93"})
    app.act("approve", {"approval": review["id"]})
    returned = app.act("return-reserve", quote(app))
    assert replay(returned)["returned_usdc_raw"] == 7990


def test_sample_verification_uses_only_bundled_signed_loads():
    rows = trial_data()[1]
    sample = rows["SYN-004"]
    assert verified_sample("SYN-004")["vendor_received_usdc_raw"] == sample["reserve_payment_cents"] * 10000
    receipt = sample["fork_receipt"]
    terms = receipt["decision"]
    selected = {**terms["selected"], "balances_cents": {"0": 1000}}
    with pytest.raises(ValueError, match="Bill terms"):
        replay({**receipt, "decision": {**terms, "selected": selected}})
    for load in ["unknown", next(k for k, row in rows.items() if not row["fork_receipt"])]:
        with pytest.raises(ValueError, match="no signed"):
            verified_sample(load)
