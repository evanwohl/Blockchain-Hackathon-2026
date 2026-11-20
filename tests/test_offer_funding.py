import pytest

from freightos.offer_funding import compare
from freightos.public_offer import price_offer


@pytest.mark.parametrize(("cash", "available", "choice", "take_fee", "wait_fee", "draw"), [
    (0, True, "early", 849, 1345, 57000),
    (20000, True, "early", 797, 1293, 37000),
    (56999, True, "early", 701, 1000, 1),
    (57000, True, "early", 500, 1000, 0),
    (58500, True, "early", 500, 1000, 0),
    (0, False, "wait", 15500, 15000, 0),
    (20000, False, "wait", 15500, 15000, 0),
])
def test_same_capital_changes_the_offer_and_withdrawal(cash, available, choice, take_fee, wait_fee, draw):
    q = compare(cash, available)
    assert q["choice"] == choice
    assert (q["early"]["cost_cents"], q["wait"]["cost_cents"]) == (take_fee, wait_fee)
    assert q[choice]["reserve_cents"] == draw
    assert q["cash_limit_cents"] + q["reserve_limit_cents"] <= price_offer()[1]["peak_reserve_cents"]
    for plan in (q["early"], q["wait"]):
        assert sum(plan[k] for k in ("cash_cents", "reserve_cents", "customer_cents", "freight_cents")) == q["bill_cents"]
        assert plan["cash_cents"] <= cash and plan["reserve_cents"] <= plan["peak_draw_cents"] <= q["reserve_limit_cents"]
        assert len(plan["scenario_costs"]) == q["budgets"] == 6
    if cash == 57000:
        assert q[choice]["peak_draw_cents"] == 1500 and q[choice]["peak_bill_cents"] == 76000
    if choice == "wait":
        assert q[choice]["early_freight_cents"] == 285000 and q[choice]["freight_cents"] == 74500
    if cash == 0 and available:
        assert q["least_early_saving_cents"] == price_offer()[2]["least_saved_cents"]


@pytest.mark.parametrize(("cash", "available"), [(-1, True), (58501, True), (6.5, True), (True, True), (0, "yes")])
def test_comparison_requires_a_valid_split(cash, available):
    with pytest.raises(ValueError):
        compare(cash, available)
