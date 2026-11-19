import pytest

from freightos.app import LoadSettlement
from freightos.surcharge import standard_terms_threshold, surcharge_plans


def test_receipt_timing_reprices_invoice_terms_without_discounting_the_bill():
    for day, draw, fee, early in [(5, 56500, 347, False), (6, 55500, 1345, True), (28, 55500, 1345, True)]:
        plans, policy = surcharge_plans(18000, day, 59999)
        assert len(plans) == 6
        for plan in plans.values():
            assert policy["elections"] == {i["invoice"]: i["option"] for i in plan["invoice_elections"]}
            assert plan["surcharge"]["for_fuel_cents"] == (0 if early else 18000)
            assert plan["calendar"]["invoices"][-1]["options"][0]["day"] == day
        plan = plans["oil stress"]
        assert plan["bill_cents"] == 74500
        assert plan["payment_cents"] == draw
        assert plan["cost_cents"] == fee
        assert (policy["elections"]["backhaul"] != "standard") == early


def test_cleared_cash_boundary_uses_redeemable_reserve_and_preserves_excess():
    for cap in [59999, 50000]:
        floor = standard_terms_threshold(cap)
        assert floor == 76000 - cap
        for cents in [floor - 1, floor]:
            plans, policy = surcharge_plans(cents, 5, cap)
            assert (policy["elections"]["backhaul"] == "standard") == (cents == floor)
            assert all(p["payment_cents"] <= cap for p in plans.values())
    plan = surcharge_plans(80000, 4, 0)[0]["oil stress"]
    assert plan["surcharge"]["for_fuel_cents"] == plan["bill_cents"]
    assert plan["invoice_elections"][-1]["receipt_cents"] == 80000
    assert plan["cost_cents"] == plan["payment_cents"] == 0


@pytest.mark.parametrize("cents,day", [(0, 5), (-1, 5), (3.5, 5), (True, 5), (100, True), (100, 0), (100, 366)])
def test_invalid_surcharge(cents, day):
    with pytest.raises(ValueError):
        surcharge_plans(cents, day, 59999)


def test_surcharge_payment_is_bound_to_the_reviewed_calendar_and_replays():
    app = LoadSettlement()
    data = {"cents": 18000, "day": 5, "scenario": "oil stress", "version": app.record_version}
    balances = lambda: (app.fork.balance(app.fork.shares), app.fork.balance(app.fork.cash))
    initial = balances()
    quote = app.act("quote-surcharge", data)
    assert quote["setup_cents"] == 58001
    assert quote["comparison_cost_cents"] == 1345
    assert app.surcharge_profile is None
    with pytest.raises(ValueError, match="again"):
        app.act("apply-surcharge", {**data, "day": 28, "id": quote["id"]})
    selected = app.act("apply-surcharge", {**data, "id": quote["id"]})
    assert selected == app.act("apply-surcharge", {**data, "id": quote["id"]})
    assert balances() == initial
    key = selected["profile"]
    assert key not in LoadSettlement().plans
    with pytest.raises(ValueError, match="changed"):
        app.act("quote-surcharge", data)
    payment = {"profile": key, "scenario": data["scenario"], "version": app.record_version}
    app.act("deposit", {**payment, "max_cents": quote["setup_cents"]})
    review = app.act("review", payment)
    with pytest.raises(ValueError, match="locked"):
        app.act("quote-surcharge", {**data, "version": app.record_version})
    receipt = app.act("approve", {"approval": review["id"]})
    assert receipt["decision"]["surcharge"]["cents"] == 18000
    assert app.act("verify", {})["vendor_received_usdc_raw"] == 565_000_000
    entry = app.act("record-payment", {**payment, "reference": "Fuel counter 581"})
    assert entry["cents"] == 18000
    app.act("reset", {"version": app.record_version})
    assert key not in app.plans and app.surcharge_profile is None
