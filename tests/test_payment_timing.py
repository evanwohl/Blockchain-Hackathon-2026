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
