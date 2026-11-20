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


def test_regional_price_gap_changes_the_portfolio_and_reserve_decision():
    reserve = {"available_cents": 3002, "road_cents": 2500}
    plan = tc.analyze(tc.recorded(), {**tc.EXAMPLE, "region": "midwest"}, reserve=reserve)
    regional = plan["regional"]
    assert (regional["training_weeks"], regional["test_weeks"]) == (97, 78)
    assert (regional["allowance_cents"], regional["oil_only_misses"], regional["allowance_misses"]) == (1100, 37, 16)
    assert plan["pump_bill_cents"] == 39990 and plan["cash_only_cents"] == 3432
    assert plan["reserve"]["before_shortfall_cents"] == 2930
    assert plan["reserve"]["plan"] is None
    split = plan["reserve"]["split"]
    assert split["outside_total_cents"] == 1003 < plan["reserve"]["before_shortfall_cents"]
    assert split["reserve_cents"] + split["outside_fuel_cents"] == plan["combined"]["residual_cents"]
    assert split["outside_total_cents"] + split["reserve_cents"] == plan["combined"]["cash_cents"]
    assert 469 < plan["combined"]["cash_cents"] < plan["best_single_cents"] <= 3432


@pytest.mark.parametrize("region", ["", "unknown", [], None])
def test_unknown_diesel_region_is_rejected(region):
    with pytest.raises(ValueError):
        tc.validate({**tc.EXAMPLE, "region": region})


def test_diesel_allowance_uses_only_prior_complete_weeks():
    from fractions import Fraction
    from freightos.regional_diesel import measure

    points = [("2024-12-09", 3000), ("2024-12-16", 3021), ("2024-12-30", 9000),
              ("2025-01-06", 4000), ("2025-01-13", 4700)]
    rows = [{"date": day, "diesel_mills": {"east": price}, "oil_cents": 7000} for day, price in points]
    stats = measure(rows, "east", Fraction(1))
    assert (stats["training_weeks"], stats["test_weeks"]) == (1, 1)
    assert stats["cents_per_gallon"] == 3
    assert stats["allowance_misses"] == 1 and stats["worst_miss_cents"] == 67


@pytest.mark.parametrize("cap", [0, 1, 2, 3, 8, 50])
def test_all_in_spend_limit_matches_exhaustive_positions(cap):
    offers = [{"price": 37, "depth": 12}, {"price": 9, "depth": 19}]
    gaps = [2, 11, 24]
    admissible = []
    for a, b in product(range(13), range(20)):
        upfront = sum(tc.cost(a, offers[0])) + sum(tc.cost(b, offers[1]))
        if upfront <= cap:
            admissible.append((upfront, max(2, 11 - a, 24 - a - b)))
    best = tc.portfolio(offers, gaps, spend_limit=cap)
    assert best["upfront_cents"] <= cap
    assert best["cash_cents"] == min(upfront + residual for upfront, residual in admissible)
    funded = tc.portfolio(offers, gaps, spend_limit=cap, fuel_cash=7)
    premiums = [upfront for upfront, residual in admissible if residual <= 7]
    if premiums:
        assert funded["upfront_cents"] == min(premiums) and funded["residual_cents"] <= 7
    else:
        assert funded is None


def test_midwest_spend_limit_reprices_the_split_payment():
    trip = {**tc.EXAMPLE, "region": "midwest", "spend_limit_cents": 300}
    r = tc.analyze(tc.recorded(), trip, reserve={"available_cents": 3002, "road_cents": 2500})
    assert r["reserve"]["split"] == {"reserve_cents": 502, "outside_fuel_cents": 792,
                                     "upfront_cents": 300, "outside_total_cents": 1092}
    assert r["limit_comparison"] == {"unrestricted_upfront_cents": 930, "kept_upfront_cents": 630, "outside_change_cents": 89}
    assert r["combined"]["cash_cents"] == 1594 < r["best_single_cents"] == 2095


@pytest.mark.parametrize(("limit", "fits"), [(197, False), (198, True)])
def test_spend_limit_one_cent_either_side_of_minimum_premium(limit, fits):
    r = tc.analyze(tc.recorded(), {**tc.EXAMPLE, "spend_limit_cents": limit},
                   reserve={"available_cents": 3002, "road_cents": 2500})
    assert r["reserve"]["minimum_spend_cents"] == 198
    assert bool(r["reserve"]["plan"]) == fits
    if not fits:
        assert r["reserve"]["split"]["outside_fuel_cents"] == 1


@pytest.mark.parametrize("limit", [-1, None, 0.3, True, 100_000_001])
def test_spending_limit_requires_whole_nonnegative_cents(limit):
    with pytest.raises(ValueError):
        tc.validate({**tc.EXAMPLE, "spend_limit_cents": limit})
