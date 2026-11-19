import pytest

from freightos.app import LoadSettlement, stressed_trial, trial_data
from freightos.funding import BACKUPS, demo_plans
from hedging.payment_timing import reserve_deadline
from hedging.quick_pay import price_calendar


def test_deadline_uses_all_dated_obligations_and_protected_cash():
    case = {"opening_cash_cents": 60, "reserve_floor_cents": 10, "bills": [
        {"id": "dispatch", "day": 3, "cents": 50}, {"id": "fuel", "day": 9, "earliest_day": 7, "cents": 40}],
        "funding": [{"id": "reserve", "available_day": 2, "restore_day": 14}]}
    payments = [{"invoice": "haul", "day": 7, "receipt_cents": 10}]
    draw = [{"source": "reserve", "net_proceeds_cents": 30, "cost_cents": 1}]
    assert reserve_deadline(case, payments, draw, "reserve") == {
        "arrival_day": 2, "last_day": 7, "first_short_day": 7, "shortfall_cents": 30}
    assert reserve_deadline(case, payments, [], "reserve") is None


def test_all_backups_use_external_funds_before_the_bill_is_due():
    plans, edges, _, _ = demo_plans()
    for profile, key in BACKUPS.items():
        for scenario, regular in plans[profile].items():
            fallback = plans[key][scenario]
            cutoff = edges[profile][scenario]["timing"]
            assert (cutoff["arrival_day"], cutoff["last_day"], cutoff["shortfall_cents"]) == (4, 5, regular["payment_cents"])
            assert cutoff["extra_fee_cents"] == fallback["cost_cents"] - regular["cost_cents"]
            assert fallback["cost_cents"] == regular["alternative_cost_cents"]
            assert fallback["payment_cents"] == 0 and fallback["other_funds_cents"] == regular["bill_cents"]
            assert min(price_calendar(fallback["calendar"])["balances_cents"].values()) >= 0


def test_backup_can_be_recorded_and_undone_without_touching_wallet():
    app = LoadSettlement()
    data = {"profile": "invoice backup", "scenario": "oil stress", "version": app.record_version}
    assert app.state()["setup_cents"]["invoice backup"] == 0
    with pytest.raises(ValueError, match="backup"):
        app.act("review", data)
    entry = app.act("record-payment", {**data, "reference": "demo-broker-41"})
    assert entry["cents"] == 74500
    state = app.state()
    assert state["manual_payment"] == entry and state["wallet_usdc_raw"] == 600_000_000
    assert not state["receipt"] and not state["deposit"]
    app.act("undo-record", data)
    assert app.state()["manual_payment"] is None


def test_one_day_stress_matches_full_repricing_and_archived_ledgers():
    _, rows = trial_data()
    observed = {"misses": 0, "shortfall_cents": 0, "replanning_fee_cents": 0}
    for row in rows.values():
        case = row["case"]
        reserve, *others = case["funding"]
        late = reserve["available_day"] + 1
        delayed = price_calendar({**case, "funding": [{**reserve, "available_day": late}, *others]})
        original = row["decision"]
        observed["replanning_fee_cents"] += delayed["cost_cents"] - original["cost_cents"]
        draw = sum(d["net_proceeds_cents"] for d in original["funding"] if d["source"] == reserve["id"])
        deficits = [case["reserve_floor_cents"] - (balance - draw) for day, balance in
                    sorted(original["balances_cents"].items(), key=lambda item: int(item[0]))
                    if reserve["available_day"] <= int(day) < late and balance - draw < case["reserve_floor_cents"]]
        if deficits:
            observed["misses"] += 1
            observed["shortfall_cents"] += deficits[0]
    assert stressed_trial()["one_day_delay"] == observed == {
        "misses": 11, "shortfall_cents": 373805, "replanning_fee_cents": 17203}


def test_late_main_invoice_keeps_fuel_paid_and_restores_reserve_across_market_levels():
    from freightos.funding import DELAY_DAYS, LATE_PLANS
    from hedging.payment_timing import invoice_delay_gap

    plans, edges, _, policies = demo_plans()
    for regular, late in LATE_PLANS.items():
        assert policies[late]["elections"]["primary load"] == "seven days"
        for key, original in plans[regular].items():
            slower = plans[late][key]
            stress = edges[regular][key]["invoice_delay"]
            assert stress["first_short_day"] == 28
            assert slower["cost_cents"] == original["cost_cents"] + 9000
            assert slower["payment_cents"] == original["payment_cents"]
            assert invoice_delay_gap(slower["calendar"], slower["invoice_elections"], slower["funding"],
                                     "primary load", DELAY_DAYS)["extra_cash_cents"] == 0
    assert edges["quick pay"]["oil stress"]["invoice_delay"]["extra_cash_cents"] == 55845
    assert edges["road cash"]["oil stress"]["invoice_delay"]["extra_cash_cents"] == 55102


def test_delayed_invoice_plan_replays_and_trial_gaps_match_saved_balances():
    from freightos.verify_receipt import replay

    app = LoadSettlement()
    profile = "quick pay late freight"
    app.act("deposit", {"profile": profile, "max_cents": 57001, "version": app.record_version})
    review = app.act("review", {"profile": profile, "scenario": "oil stress"})
    receipt = app.act("approve", {"approval": review["id"]})
    assert replay(receipt)["vendor_received_usdc_raw"] == 555000000
    assert receipt["decision"]["cost_cents"] == 10345
    _, cases = trial_data()
    gaps = []
    for row in cases.values():
        decision = row["decision"]
        primary = next(p for p in decision["payments"] if p["invoice"] == "primary load")
        if primary["option"] == "standard":
            floor = row["case"]["reserve_floor_cents"]
            gaps.append(max(0, *(floor - balance + primary["receipt_cents"] for day, balance
                                 in decision["balances_cents"].items() if primary["day"] <= int(day) < primary["day"] + 7)))
    trial = stressed_trial()["late_freight"]
    assert (trial["short_cases"], trial["peak_gaps_cents"]) == (sum(g > 0 for g in gaps), sum(gaps))


def test_owner_can_refill_later_at_recorded_carry_cost_and_settle_the_same_fuel_amount():
    from freightos.funding import REFILL_LATER
    from freightos.verify_receipt import replay
    from hedging.payment_timing import later_refill_calendar

    app = LoadSettlement()
    choices = app.plans[REFILL_LATER]
    for key, plan in choices.items():
        original = app.plans["quick pay"][key]
        assert plan["payment_cents"] == original["payment_cents"]
        assert plan["refill_policy"]["extra_term_bps"] == 7
        assert [(i["option"], i["day"]) for i in plan["invoice_elections"]] == [("standard", 35), ("one day", 1)]
        assert plan["cost_cents"] == 1200 + (plan["payment_cents"] * 33 + 9999) // 10000
        assert min(price_calendar(plan["calendar"])["balances_cents"].values()) >= 0
    assert choices["oil stress"]["cost_cents"] == 1384
    app.act("deposit", {"profile": REFILL_LATER, "max_cents": 57001, "version": app.record_version})
    review = app.act("review", {"profile": REFILL_LATER, "scenario": "oil stress"})
    receipt = app.act("approve", {"approval": review["id"]})
    assert replay(receipt)["vendor_received_usdc_raw"] == 555000000
    credit = app.plans["credit line"]["oil stress"]["calendar"]
    moved = later_refill_calendar(credit, "reserve redemption", 7, 7)
    assert moved["funding"][1] == credit["funding"][1]
    assert moved["funding"][0]["restore_day"] == credit["funding"][0]["restore_day"] + 7


def test_optional_refill_savings_reconcile_to_two_late_customer_deadlines():
    from freightos.funding import DELAY_DAYS
    from hedging.payment_timing import late_invoice_calendar, later_refill_calendar

    _, cases = trial_data()
    savings = []
    for row in cases.values():
        late = late_invoice_calendar(row["case"], "primary load", DELAY_DAYS)
        fixed = price_calendar(late)
        flexible = price_calendar(later_refill_calendar(late, "Kamino reserve", DELAY_DAYS, 7))
        savings.append(max(0, fixed["cost_cents"] - flexible["cost_cents"]))
    report = stressed_trial()["flexible_refill"]
    assert sum(savings) == report["savings_cents"] == 347094
    assert sum(s > 0 for s in savings) == report["cheaper_cases"] == 39


def test_next_expense_protects_future_fees_and_accepts_same_day_receipts():
    from hedging.payment_timing import spendable_on

    calendar = {"opening_cash_cents": 140, "reserve_floor_cents": 20,
                "funding": [{"id": "line", "available_day": 1, "restore_day": 6}],
                "bills": [{"day": 7, "earliest_day": 5, "cents": 5, "max_cents": 25}]}
    draws = [{"source": "line", "net_proceeds_cents": 30, "cost_cents": 3}]
    receipts = [{"day": 4, "receipt_cents": 18}, {"day": 10, "receipt_cents": 50}]
    assert [spendable_on(calendar, receipts, draws, day) for day in [0, 4, 6, 9, 10, 20]] == [110, 110, 110, 110, 160, 160]


def test_next_trip_limits_match_repricing_with_one_extra_bill():
    from freightos.funding import REFILL_LATER

    app = LoadSettlement()
    state = app.state()
    comparison = state["next_trip"]
    assert comparison["budgets"] == 6
    assert [row["capacity_cents"] for row in comparison["options"]] == [2999, 236650, 289000]
    for option in comparison["options"]:
        profile = option["profile"]
        expected_terms = None
        has_boundary = False
        for p in app.plans[profile].values():
            elections = {e["invoice"]: e["option"] for e in p["invoice_elections"]}
            assert expected_terms in (None, elections)
            expected_terms = elections
            case = p["calendar"]
            fixed_invoices = [{**invoice, "options": [o for o in invoice["options"]
                               if o["name"] == elections[invoice["id"]]]} for invoice in case["invoices"]]
            decision = price_calendar({**case, "invoices": fixed_invoices})
            assert decision["funding"] == p["funding"]
            ledger = decision["balances_cents"]
            day = comparison["day"]
            at_day = ledger[max(d for d in ledger if d <= day)]
            balances = [at_day, *(v for d, v in ledger.items() if d > day)]
            cash_after_setup = state["wallet_usdc_raw"] // 10000 - state["setup_cents"][profile]
            remaining = min(balances) + cash_after_setup - option["capacity_cents"]
            assert remaining >= case["reserve_floor_cents"]
            has_boundary |= remaining == case["reserve_floor_cents"]
        assert has_boundary
    app.act("deposit", {"profile": REFILL_LATER, "max_cents": 57001, "version": app.record_version})
    after = app.state()["next_trip"]["options"]
    assert after[0]["capacity_cents"] == 2999
    assert after[-1]["capacity_cents"] == 289000 - 57001
    review = app.act("review", {"profile": REFILL_LATER, "scenario": "oil stress"})
    assert app.state()["next_trip"] is None
    app.act("approve", {"approval": review["id"]})
    assert app.state()["next_trip"] is None


def test_next_trip_excludes_a_plan_that_changes_invoice_terms_after_observing_fuel():
    from freightos.funding import LATE_PLANS, next_trip_options

    plans, _, _, _ = demo_plans()
    backup = BACKUPS[LATE_PLANS["quick pay"]]
    option = plans[backup]["oil stress"]
    elections = [{**e, "option": "standard"} for e in option["invoice_elections"]]
    changed = {**plans, backup: {**plans[backup], "oil stress": {**option, "invoice_elections": elections}}}
    assert not next_trip_options(changed, {backup: 0}, 600000000)["options"]
