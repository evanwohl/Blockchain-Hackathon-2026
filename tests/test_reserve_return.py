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
    assert q["remaining_value_raw"] >= q["protected_cents"] * 10000 == 464_510_000
    next_release = reserve_return.withdraw(app.fork, q["return_shares_raw"] + 1)
    left = reserve_return.withdraw(app.fork, q["shares_before_raw"] - q["return_shares_raw"] - 1)
    assert (reserve_return.simulated_cash(app.fork, [next_release, left])
            - reserve_return.simulated_cash(app.fork, [next_release])) < 464_510_000
    review = app.act("review", {"profile": "road cash", "scenario": "oil stress"})
    with pytest.raises(ValueError, match="pending"):
        app.act("return-reserve", data)
    app.act("cancel", {"approval": review["id"]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: app.act("return-reserve", data), range(2)))
    assert results[0] == results[1]
    proof = replay(results[0])
    assert proof["returned_usdc_raw"] == 135_487_990
    assert proof["protected_value_usdc_raw"] == 464_510_000
    assert app.state()["setup_cents"]["road cash"] == 0
    review = app.act("review", {"profile": "road cash", "scenario": "wti-93"})
    paid = app.act("approve", {"approval": review["id"]})
    assert replay(paid)["vendor_received_usdc_raw"] == 464_510_000


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
        if target == 59951:
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


def test_next_trip_releases_only_the_shortfall_and_preserves_each_fuel_budget():
    app = LoadSettlement()
    version = app.record_version
    app.act("deposit", {"profile": "credit line", "max_cents": 59952, "version": version})
    check = app.state()["next_trip"]
    flex = next(row for row in check["options"] if row["profile"] == "later reserve refill")
    assert (flex["capacity_cents"], flex["returnable_cents"], check["return_yield_bps"]) == (48, 2951, 25)
    body = {"profile": flex["profile"], "version": version, "extra_wallet_cents": 2000 - flex["capacity_cents"]}
    proposal = app.act("return-quote", body)
    fewer = reserve_return.withdraw(app.fork, proposal["return_shares_raw"] - 1)
    assert reserve_return.simulated_cash(app.fork, [fewer]) < 20_000_000
    assert app.state()["wallet_usdc_raw"] == 480000
    with pytest.raises(ValueError, match="cannot cover"):
        app.act("return-quote", {**body, "extra_wallet_cents": 2952})
    with pytest.raises(ValueError, match="Review"):
        app.act("return-reserve", {**body, "extra_wallet_cents": 1900, "id": proposal["id"]})
    receipt = app.act("return-reserve", {**body, "id": proposal["id"]})
    assert app.act("return-reserve", {**body, "id": proposal["id"]}) == receipt
    proof = replay(receipt)
    assert proof["returned_usdc_raw"] == 19_520_000 and proof["wallet_usdc_raw"] == 20_000_000
    assert proof["protected_value_usdc_raw"] >= 570_000_000
    state = app.state()
    assert state["last_return"]["id"] == proposal["id"] and state["next_trip"]["options"][0]["capacity_cents"] == 2000
    review = app.act("review", {"profile": flex["profile"], "scenario": "wti-93"})
    assert replay(app.act("approve", {"approval": review["id"]}))["vendor_received_usdc_raw"] == 570_000_000


def test_requested_return_counts_existing_subcent_cash_and_invalid_quotes_stay_read_only(monkeypatch):
    app = LoadSettlement()
    app.act("deposit", {})
    initial = quote(app, "road cash")
    app.act("return-reserve", initial)
    body = {"profile": "invoice backup", "version": app.record_version, "extra_wallet_cents": 1}
    before = app.state()["wallet_usdc_raw"]
    proposal = app.act("return-quote", body)
    assert 0 < proposal["return_usdc_raw"] < 10000
    assert (before + proposal["return_usdc_raw"]) // 10000 == before // 10000 + 1
    for bad in [True, "3", -1, 0]:
        with pytest.raises(ValueError, match="positive"):
            app.act("return-quote", {**body, "extra_wallet_cents": bad})
    def unavailable(*args):
        raise reserve_return.VaultExecutionError("Unavailable withdrawal quote")
    monkeypatch.setattr(reserve_return, "return_quote", unavailable)
    app.quote_balances = None
    state = app.state()
    assert state["wallet_usdc_raw"] == before
    assert all(row["returnable_cents"] == 0 for row in state["next_trip"]["options"])


def test_remaining_shares_below_the_withdrawal_minimum_can_still_be_topped_up():
    from solders.compute_budget import set_compute_unit_limit

    app = LoadSettlement()
    app.act("deposit", {})
    fork = app.fork
    shares = fork.balance(fork.shares)
    fork.send(fork.tx([set_compute_unit_limit(1400000), reserve_return.withdraw(fork, shares - 100)]))
    assert fork.redemption_value(100) == 0
    state = app.state()
    assert state["reserve_shares_raw"] == 100
    assert state["setup_cents"]["quick pay"] == 57001
    assert all(row["returnable_cents"] == 0 for row in state["next_trip"]["options"])


@pytest.mark.parametrize(("change", "error"), [({"extra_wallet_cents": 1}, "Return size"), ({"split_cost_raw": 100_000}, "Split cost")])
def test_receipt_replay_reprices_target_even_when_incorrect_terms_were_signed(change, error):
    app = LoadSettlement()
    app.act("deposit", {})
    request = {"profile": "invoice backup", "version": app.record_version, "extra_wallet_cents": 2000}
    proposal = app.act("return-quote", request)
    proposal.pop("id")
    receipt = reserve_return.execute(app.fork, {**proposal, **change})
    with pytest.raises(ValueError, match=error):
        replay(receipt)
