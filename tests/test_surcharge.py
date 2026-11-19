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


def test_offer_fee_frontier_uses_the_same_invoice_terms_across_fuel_outcomes():
    from freightos.surcharge import offer_decision

    for fee, expected_cost, min_saved in [(500, 849, 496), (994, 1344, 1), (995, 1345, 0), (1200, 1551, -206)]:
        plans, policy, offer = offer_decision(18000, 5, 28, fee, 59999, "oil stress")
        assert offer["highest_fee_cents"] == 994
        assert offer["early_cost_cents"] == expected_cost
        assert offer["least_saved_cents"] == min_saved
        assert offer["take_offer"] == (fee <= 994)
        for plan in plans.values():
            assert policy["elections"] == {i["invoice"]: i["option"] for i in plan["invoice_elections"]}
            assert plan["surcharge"]["day"] == (5 if min_saved > 0 else 28)
        if fee == 500:
            assert plans["oil stress"]["payment_cents"] == 57000
            assert plans["oil stress"]["surcharge"]["for_fuel_cents"] == 17500


def test_early_offer_needs_a_real_improvement_and_valid_terms():
    from freightos.surcharge import offer_decision

    for fee, regular_day in [(-1, 28), (True, 28), (20001, 28), (10, 5), (10, 4)]:
        with pytest.raises(ValueError):
            offer_decision(18000, 5, regular_day, fee, 59999, "oil stress")
    plans, _, offer = offer_decision(18000, 6, 28, 0, 59999, "oil stress")
    assert offer["highest_fee_cents"] is None and not offer["take_offer"]
    assert plans["oil stress"]["surcharge"]["day"] == 28


def test_offer_requote_and_vendor_payment_replay():
    app = LoadSettlement()
    data = {"version": app.record_version, "cents": 18000, "scenario": "oil stress",
            "day": 5, "fee_cents": 500, "regular_day": 28}
    quote = app.act("quote-surcharge", data)
    with pytest.raises(ValueError, match="again"):
        app.act("apply-surcharge", {**data, "fee_cents": 501, "id": quote["id"]})
    chosen = app.act("apply-surcharge", {**data, "id": quote["id"]})
    plan = app.plans[chosen["profile"]]["oil stress"]
    assert plan["surcharge"]["version"] == 2 and plan["cost_cents"] == 849
    pay = {**chosen, "scenario": "oil stress", "version": app.record_version}
    app.act("deposit", {**pay, "max_cents": quote["setup_cents"]})
    approval = app.act("review", pay)
    receipt = app.act("approve", {"approval": approval["id"]})
    assert receipt["decision"]["surcharge"]["fee_cents"] == 500
    assert app.act("verify", {})["vendor_received_usdc_raw"] == 570000000
    assert app.act("record-payment", pay)["cents"] == 17500


def test_fee_ceiling_moves_when_reserve_access_or_the_surcharge_changes():
    from freightos.surcharge import offer_decision

    _, _, just_worth_it = offer_decision(16001, 5, 28, 40, 59999, "oil stress")
    _, _, too_much = offer_decision(16001, 5, 28, 41, 59999, "oil stress")
    assert just_worth_it["highest_fee_cents"] == 40
    assert just_worth_it["take_offer"] and not too_much["take_offer"]
    _, _, limited = offer_decision(18000, 5, 28, 0, 38000, "oil stress")
    assert limited["highest_fee_cents"] is None and not limited["take_offer"]


def test_driver_can_compare_and_commit_to_a_reserve_limit_without_moving_money():
    app = LoadSettlement()
    data = {"version": app.record_version, "scenario": "oil stress", "cents": 18000,
            "day": 5, "regular_day": 28, "fee_cents": 800, "reserve_limit_cents": 58500}
    shares = app.fork.balance(app.fork.shares)
    quote = app.act("quote-surcharge", data)
    assert quote["capacity_cents"] == 58500 and quote["available_reserve_cents"] == 59999
    assert quote["offer"]["highest_fee_cents"] == 500
    assert not quote["offer"]["take_offer"] and quote["cost_cents"] == 1345
    alternative = quote["offer"]["larger_budget"]
    assert alternative == {"limit_cents": 58800, "minimum_to_beat_current": True, "peak_reserve_cents": 58800, "early_cost_cents": 1149, "wait_cost_cents": 1345, "least_saved_cents": 195,
                           "setup_cents": 58801, "extra_setup_cents": 1800}
    short = app.act("quote-surcharge", {**data, "reserve_limit_cents": alternative["limit_cents"] - 1})
    assert not short["offer"]["take_offer"] and short["cost_cents"] == quote["cost_cents"]
    larger = {**data, "reserve_limit_cents": alternative["limit_cents"]}
    with pytest.raises(ValueError, match="again"):
        app.act("apply-surcharge", {**larger, "id": quote["id"]})
    revised = app.act("quote-surcharge", larger)
    assert revised["offer"]["take_offer"] and revised["offer"]["highest_fee_cents"] == 800
    assert revised["cost_cents"] == 1149 and revised["reserve_cents"] == 57300
    key = app.act("apply-surcharge", {**larger, "id": revised["id"]})["profile"]
    assert app.state()["surcharge_limit_cents"] == 58800
    assert max(p["payment_cents"] for p in app.plans[key].values()) == 58800
    assert app.fork.balance(app.fork.shares) == shares
    pay = {"profile": key, "scenario": "oil stress", "version": app.record_version}
    app.act("deposit", {**pay, "max_cents": revised["setup_cents"]})
    approval = app.act("review", pay)
    receipt = app.act("approve", {"approval": approval["id"]})
    assert receipt["decision"]["surcharge"]["capacity_cents"] == 58800
    assert app.act("verify", {})["vendor_received_usdc_raw"] == 573_000_000
    app.act("reset", {"version": app.record_version})
    assert app.surcharge_limit is None


def test_optional_budget_accepts_zero_and_never_exceeds_wallet_capacity():
    app = LoadSettlement()
    data = {"version": app.record_version, "scenario": "oil stress", "cents": 18000, "day": 5}
    for invalid in (-1, True, 2.5, None):
        with pytest.raises(ValueError, match="reserve limit"):
            app.act("quote-surcharge", {**data, "reserve_limit_cents": invalid})
    zero = app.act("quote-surcharge", {**data, "reserve_limit_cents": 0})
    assert zero["capacity_cents"] == zero["reserve_cents"] == zero["setup_cents"] == 0
    high = app.act("quote-surcharge", {**data, "reserve_limit_cents": 70000})
    assert high["capacity_cents"] == 59999 and high["requested_limit_cents"] == 70000
