from copy import deepcopy
from itertools import product

import pytest

from freightos import trip_cover as tc


def test_recorded_portfolio_beats_each_single_and_reconciles_every_dollar():
    result = tc.saved()
    mix = result["combined"]
    assert (result["pump_bill_cents"], result["cash_only_cents"], result["best_single_cents"], mix["cash_cents"]) == (39990, 2332, 908, 469)
    assert mix["residual_cents"] + sum(x["fee_cents"] + x["premium_cents"] for x in mix["legs"]) == 469
    assert len(mix["legs"]) > 1 and all(0 < x["lots"] <= x["depth"] for x in mix["legs"])


@pytest.mark.parametrize("gaps", [[0, 11, 24], [5, 11, 24], [0, 0, 0], [2, 4, 12], [10, 11, 11]])
def test_integer_solution_matches_every_permitted_position(gaps):
    offers = [{"price": 37, "depth": 12}, {"price": 9, "depth": 19}]
    best = gaps[-1]
    for a, b in product(range(13), range(20)):
        cost = sum(tc.cost(a, offers[0])) + sum(tc.cost(b, offers[1]))
        best = min(best, cost + max(gaps[0], gaps[1] - a, gaps[2] - a - b))
    assert tc.portfolio(offers, gaps)["cash_cents"] == best


def test_shortfall_cash_only_when_original_quotes_expire():
    snap = {**tc.recorded(), "checked_at": "2026-11-20T22:00:00Z"}
    result = tc.analyze(snap, tc.EXAMPLE)
    assert result["offers"] == 0 and result["combined"]["cash_cents"] == result["cash_only_cents"] == 2332


def test_one_disagreeing_order_book_cannot_be_used():
    snap = deepcopy(tc.recorded())
    sym = "GEMI-WTI2611202200-HI87"
    snap["books"][sym]["levels"]["asks"][0]["price"] = ".99"
    result = tc.analyze(snap, tc.EXAMPLE)
    assert result["offers"] == tc.saved()["offers"] - 1
    assert all(x["symbol"] != sym for x in result["combined"]["legs"])


def test_user_budget_and_pump_sign_change_the_actual_comparison():
    trip = {**tc.EXAMPLE, "gallons": 50, "budget_cents": 30000}
    r = tc.analyze(tc.recorded(), trip)
    assert r["pump_bill_cents"] == 19995 and r["cash_only_cents"] == 0 and not r["combined"]["legs"]


@pytest.mark.parametrize(("field", "value"), [("gallons", 1.5), ("gallons", 0), ("pump_mills", True), ("budget_cents", -1)])
def test_invalid_trip_amounts(field, value):
    with pytest.raises(ValueError):
        tc.validate({**tc.EXAMPLE, field: value})


@pytest.mark.parametrize("limit", [0, 2, 8, 15, 24, 25])
def test_minimum_premium_under_reserve_limit_matches_exhaustive_search(limit):
    offers = [{"price": 37, "depth": 12}, {"price": 9, "depth": 19}]
    gaps = [2, 11, 24]
    costs = [sum(tc.cost(a, offers[0])) + sum(tc.cost(b, offers[1]))
             for a, b in product(range(13), range(20)) if max(2, 11 - a, 24 - a - b) <= limit]
    result = tc.portfolio(offers, gaps, fuel_cash=limit)
    if costs:
        assert result["upfront_cents"] == min(costs)
        assert result["residual_cents"] <= limit
    else:
        assert result is None


def test_separate_market_cash_and_road_cash_reconcile_to_the_recorded_reserve():
    result = tc.analyze(tc.recorded(), tc.EXAMPLE, reserve={"available_cents": 3002, "road_cents": 2500, "slot": 1})
    reserve = result["reserve"]
    assert reserve["before_shortfall_cents"] == 1830
    assert reserve["fuel_cash_cents"] == reserve["plan"]["residual_cents"] == 502
    assert reserve["plan"]["upfront_cents"] == 198 < result["combined"]["upfront_cents"]
    assert reserve["plan"]["cash_cents"] == 700


def test_road_cash_alone_can_exhaust_the_reserve():
    result = tc.analyze(tc.recorded(), tc.EXAMPLE, reserve={"available_cents": 3002, "road_cents": 3100, "slot": 1})
    assert result["reserve"]["plan"] is None and result["reserve"]["before_shortfall_cents"] == 2430
