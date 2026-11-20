from datetime import date
from fractions import Fraction
from itertools import product

from freightos import fill_replay, trip_cover


def test_replay_split_release_lag_and_exact_strike(monkeypatch):
    dates = ["2024-12-30", "2025-01-06", "2025-01-13", "2025-01-20"]
    monkeypatch.setattr(fill_replay, "changes", lambda region: [
        (date.fromisoformat(end), Fraction(1), 51) for end in dates])
    trip = dict(region="midwest", pump_mills=4000, gallons=5, budget_cents=1980, pump_cash_cents=10)
    offers = [{"strike": "81"}]
    early, later = fill_replay.fills(trip, Fraction(80), offers, 20, date(2025, 1, 20))
    assert [r[2] for r in early] == dates[:1]
    assert later == [(1, 16, "2025-01-13", 1, 51, 2026)]
    plan = {"legs": [{"strike": "81", "lots": 11}], "upfront_cents": 3}
    result = fill_replay.summary(later, offers, plan)
    assert result["peak"]["short_cents"] == 5
    assert result["tail_cash_cents"] == 8
    assert fill_replay.tail_sum([10, 7] + [0]*9) == 107
    assert fill_replay.tail_sum([10]*10) == 100


def test_tail_portfolio_matches_exhaustive_search():
    offers = [{"price": 32, "depth": 16}, {"price": 8, "depth": 22}]
    gaps = [2, 11, 21]
    samples = [(2, 36), (2, 24), (1, 14), (0, 9)] + [(0, 0)]*13

    def score(a, b):
        premium = sum(trip_cover.cost(a, offers[0])) + sum(trip_cover.cost(b, offers[1]))
        worst = sorted((max(0, need - (0, a, a+b)[band]) for band, need in samples), reverse=True)
        return premium*17 + 10*worst[0] + 7*worst[1]

    feasible = [(a, b) for a, b in product(range(17), range(23))
                if max(2, 11-a, 21-a-b) <= 11
                and sum(trip_cover.cost(a, offers[0])) + sum(trip_cover.cost(b, offers[1])) <= 10]
    plan = trip_cover.portfolio(offers, gaps, fuel_cash=11, spend_limit=10, tail_samples=samples)
    lots = {leg["price"]: leg["lots"] for leg in plan["legs"]}
    assert score(lots.get(32, 0), lots.get(8, 0)) == min(score(*pair) for pair in feasible)


def test_sample_tradeoff_and_separate_premium():
    trip = dict(trip_cover.EXAMPLE, region="midwest", diesel_model="recent", pump_cash_cents=500, spend_limit_cents=300)
    reserve = {"available_cents": 3002, "road_cents": 2500}
    target = trip_cover.analyze(trip_cover.recorded(), trip, reserve=reserve)["pump_target"]
    original = target["fill_replay"]
    fitted = target["history_mix"]
    assert original["before"]["weeks"] == 78
    assert original["after"]["peak"]["short_cents"] == 5659
    assert fitted["training_weeks"] == 97
    assert fitted["train_cents"] < fitted["original_train_cents"]
    assert fitted["plan"]["upfront_cents"] <= target["plan"]["upfront_cents"]
    assert fitted["after"]["short_cents"] <= target["after"]["short_cents"] + trip["pump_mills"] // 10
    peak = fitted["fill_replay"]["after"]["peak"]
    assert peak["short_cents"] == peak["before_payout_cents"] - peak["payout_cents"]
    assert fitted["fill_replay"]["before"] == original["before"]


def test_month_sensitivity_surfaces_dependence_on_one_extreme_month():
    samples = [(1, 100, "2025-03-03"), (0, 0, "2025-04-07"), (0, 0, "2025-05-05")]
    offers = [{"strike": "88"}]
    cash = {"upfront_cents": 0, "legs": []}
    cover = {"upfront_cents": 10, "legs": [{"strike": "88", "lots": 80}]}
    assert fill_replay.month_sensitivity(samples, offers, cash, cover) == {"months": 3, "lower": 2}
    assert fill_replay.month_sensitivity(samples[:1], offers, cash, cover) is None
