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


@pytest.mark.parametrize(("pump", "cap"), [(0, 0), (2, 2), (6, 3), (7, 12), (24, 0), (0, None)])
def test_pump_target_matches_all_positions_with_independent_cash_limits(pump, cap):
    offers = [{"price": 37, "depth": 12}, {"price": 9, "depth": 19}]
    reserve = 3
    result = tc.pump_target(offers, [2, 11, 24], pump, reserve, cap)
    outcomes = [(sum(tc.cost(a, offers[0])) + sum(tc.cost(b, offers[1])), max(2, 11 - a, 24 - a - b), a, b)
                for a, b in product(range(13), range(20))]
    fitting = [o for o in outcomes if o[1] <= pump + reserve]
    assert result["minimum_spend_cents"] == min((o[0] for o in fitting), default=None)
    assert result["single_spend_cents"] == min((o[0] for o in fitting if not o[2] or not o[3]), default=None)
    allowed = [o for o in outcomes if cap is None or o[0] <= cap]
    best = min((max(0, residual - pump - reserve), upfront) for upfront, residual, _, _ in allowed)
    assert (result["after"]["short_cents"], result["plan"]["upfront_cents"]) == best
    assert result["after"]["reserve_cents"] + result["after"]["outside_cents"] == result["plan"]["residual_cents"]


def test_recorded_midwest_minimum_for_five_more_dollars_at_the_stop():
    trip = {**tc.EXAMPLE, "region": "midwest", "pump_cash_cents": 500}
    reserve = {"available_cents": 3002, "road_cents": 2500}
    q = tc.analyze(tc.recorded(), trip, reserve=reserve)["pump_target"]
    assert q["minimum_spend_cents"] == q["plan"]["upfront_cents"] == 555
    assert q["after"] == {"reserve_cents": 502, "outside_cents": 500, "short_cents": 0}
    assert q["before"]["short_cents"] == 2430
    lower = tc.analyze(tc.recorded(), {**trip, "spend_limit_cents": 554}, reserve=reserve)["pump_target"]
    assert lower["after"]["short_cents"] > 0


@pytest.mark.parametrize("amount", [-1, None, True, 0.5, 100_000_001])
def test_pump_money_requires_whole_nonnegative_cents(amount):
    with pytest.raises(ValueError):
        tc.validate({**tc.EXAMPLE, "pump_cash_cents": amount})


def test_debt_free_comparison_reconciles_every_settlement_band_and_increment():
    trip = {**tc.EXAMPLE, "region": "midwest", "pump_cash_cents": 500, "spend_limit_cents": 300}
    reserve = {"available_cents": 3002, "road_cents": 2500}
    target = tc.analyze(tc.recorded(), trip, reserve=reserve)["pump_target"]
    change = target["protection_change"]
    assert change["total_cents"] == 555 and change["extra_cents"] == 255 and target["after"]["short_cents"] == 292
    other = tc.analyze(tc.recorded(), {**trip, "spend_limit_cents": 555}, reserve=reserve)["pump_target"]
    assert other["after"]["short_cents"] == 0 and other["protection_change"] is None
    for row in change["outcomes"]:
        def payout(plan):
            return sum(leg["lots"] for leg in plan["legs"] if row["from_strike"] and float(leg["strike"]) <= float(row["from_strike"]))
        assert row["extra_payout_cents"] == payout(other["plan"]) - payout(target["plan"])
    assert change["market_value"] == {"low_cents": 239, "high_cents": 252}
    assert 0 <= change["draw_low_cents"] <= change["draw_high_cents"] <= target["after"]["short_cents"]
    assert max(row["max_draw_cents"] for row in change["outcomes"]) == 292
    assert all(0 <= row["min_draw_cents"] <= row["max_draw_cents"] for row in change["outcomes"])


@pytest.mark.parametrize("changes", [[11, -8, 37], [-100, 12, -24], [1, 0, 0]])
def test_nested_quote_bounds_match_independent_enumeration(changes):
    offers = [{"bid": 3, "price": 8}, {"bid": 1, "price": 7}, {"bid": 4, "price": 6}]
    possible = [sum(p*q for p, q in zip(prices, changes)) for prices in product(*(range(o["bid"], o["price"]+1) for o in offers))
                if prices[0] >= prices[1] >= prices[2]]
    assert tc.market_value_range(offers, changes) == {"low_cents": min(possible)//100, "high_cents": -(-max(possible)//100)}
    assert tc.market_value_range([{"bid": 1, "price": 2}, {"bid": 3, "price": 4}], [1, 1]) is None


@pytest.mark.parametrize("bad", ["0.82", "0.98", "NaN"])
def test_unverified_bid_suppresses_mark_but_keeps_executable_ask(bad):
    snapshot = deepcopy(tc.recorded())
    snapshot["books"]["GEMI-WTI2611202200-HI81"]["levels"]["bids"][0]["price"] = bad
    result = tc.analyze(snapshot, {**tc.EXAMPLE, "region": "midwest", "pump_cash_cents": 500, "spend_limit_cents": 300},
                        reserve={"available_cents": 3002, "road_cents": 2500})
    change = result["pump_target"]["protection_change"]
    assert change["total_cents"] == 555 and change["market_value"] is None


def test_draw_value_keeps_within_band_uncertainty_and_price_order():
    offers = [{"strike": "1", "bid": 50, "price": 50}, {"strike": "2", "bid": 20, "price": 20}]
    selected = {"upfront_cents": 1, "legs": [{"strike": "1", "lots": 10}]}
    larger = {"upfront_cents": 2, "legs": [{"strike": "1", "lots": 11}, {"strike": "2", "lots": 5}]}
    change = tc.protection_change(offers, [20, 40, 60], 10, selected, larger)
    assert [(o["min_draw_cents"], o["max_draw_cents"]) for o in change["outcomes"]] == [(0, 10), (0, 20), (20, 40)]
    assert (change["draw_low_cents"], change["draw_high_cents"]) == (4, 19)


@pytest.mark.parametrize("peak", [10, 13, 18])
def test_robust_funding_risk_and_peak_tiebreak_match_every_hedge(peak):
    offers = [{"strike": "1", "bid": 47, "price": 52, "depth": 14},
              {"strike": "2", "bid": 23, "price": 35, "depth": 13}]
    gaps = [3, 12, 24]
    cash, spend = 7, 10
    candidates = []
    for a, b in product(range(15), range(14)):
        residuals = [3, 12-a, 24-a-b]
        premium = sum(tc.cost(a, offers[0])) + sum(tc.cost(b, offers[1]))
        if max(residuals) <= peak and premium <= spend:
            hit = [int(v > cash) for v in residuals]
            weight = max((100-x)*hit[0] + (x-y)*hit[1] + y*hit[2] for x, y in product([47, 52], [23, 35]))
            candidates.append((weight, max(residuals)))
    args = dict(fuel_cash=peak, spend_limit=spend, risk_capacity=cash)
    first = tc.portfolio(offers, gaps, **args)
    if not candidates:
        assert first is None
        return
    optimal_weight, optimal_peak = min(candidates)
    best = tc.portfolio(offers, gaps, risk_limit=optimal_weight, **args)
    for solution in (first, best):
        lots = {leg["strike"]: leg["lots"] for leg in solution["legs"]}
        assert tc.funding_weight(offers, gaps, [lots.get("1", 0), lots.get("2", 0)], cash) == optimal_weight
        assert solution["upfront_cents"] <= spend
    assert best["residual_cents"] == optimal_peak


def test_four_fill_expected_shortfall_uses_fractional_tail_and_excludes_gaps():
    from datetime import date, timedelta
    from fractions import Fraction
    start = date(2025, 2, 3)
    rows = [((start + timedelta(weeks=i)).isoformat(), Fraction(i)) for i in range(15)]
    rows.append(((start + timedelta(weeks=16)).isoformat(), Fraction(1000)))
    stress = tc.regional_diesel.consecutive_fill_stress(rows, 5)
    assert stress["windows"] == 12 and stress["overruns"] == 9
    assert Fraction(stress["tail_cents_per_weekly_gallon"]) == (30 + Fraction(26, 5)) / Fraction(6, 5)
    assert tc.regional_diesel.consecutive_fill_stress(rows[:3], 5) is None
