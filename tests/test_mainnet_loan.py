import pytest

from freightos import mainnet_loan, road_cash


def quote(budget, cash=7500, stress=15000):
    return road_cash.evaluate(budget, cash, 20000, 300, stress)["mainnet"]["secured_loan"]


def test_minimum_budget_and_minimum_debt_on_the_fuel_stop():
    q = quote(8000)
    assert q["selected"] is q["best_funded"] is None
    minimum = q["minimum"]["opening_cents"]
    assert minimum == 19293 and quote(minimum - 1)["selected"] is None
    r = quote(minimum)["selected"]
    assert r == q["minimum"] and r["high_payday_cents"] == 300
    assert r["draw_cents"] == 8884 and r["repay_cents"] == 8962
    assert q["cash_cents"] + q["stable_cents"] + r["draw_cents"] + r["sale_cents"] == 19000
    assert r["low_payday_cents"] < q["sell_only"]["low_payday_cents"]
    assert r["high_payday_cents"] > q["sell_only"]["high_payday_cents"]
    modeled = quote(minimum)
    assert modeled["history"] == {"ahead": 6, "windows": 9, "ties": 0}
    assert (r["start_payday_cents"], q["sell_only"]["start_payday_cents"]) == (915, 909)
    assert 0 < r["pledge_lamports"] < r["opening_lamports"]


def test_cash_only_zero_collateral_price_and_unavailable_credit():
    q = quote(0, cash=19000)
    assert q["selected"]["opening_lamports"] == q["selected"]["repay_cents"] == 0
    assert quote(30000, stress=0)["minimum"] is None
    plan = dict(bill_cents=200, reference_cents=100, low_price_cents=100, high_price_cents=200,
                floor_cents=50, receipt_cents=200)
    lending = mainnet_loan.saved()
    for cap in (dict(credit_raw=9999), dict(deposit_room=1)):
        r = mainnet_loan.compare(plan, [0], 0, 0, 10**12, 10**12, 5000, 100, lending | cap)
        assert r["minimum"] is r["best_funded"] is None


def test_changed_lending_evidence_leaves_other_market_results_available(monkeypatch):
    with pytest.raises(ValueError, match="changed"):
        mainnet_loan.verify(mainnet_loan.EVIDENCE.read_bytes() + b" ")
    def unavailable():
        raise ValueError("changed")
    monkeypatch.setattr(mainnet_loan, "saved", unavailable)
    m = road_cash.evaluate(8000, 7500, 20000, 300, 15000)["mainnet"]
    assert m["secured_loan"] is None and m["fuel_short_cents"] == 1544


def test_one_cent_draw_is_under_the_markets_minimum_at_the_recorded_usdc_price():
    lending = mainnet_loan.saved()
    plan = dict(bill_cents=1, reference_cents=7282, low_price_cents=4984, high_price_cents=15000,
                floor_cents=0, receipt_cents=1000)
    def only_loan(bill):
        return mainnet_loan.compare(plan | {"bill_cents": bill}, [0], 0, 0, 10**9, 10**9, 5000, 0, lending)
    assert only_loan(1)["best_funded"] is None
    assert only_loan(2)["selected"]["draw_cents"] == 2
