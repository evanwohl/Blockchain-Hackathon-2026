from datetime import date, timedelta
from itertools import product

from freightos import fill_replay, payday, trip_cover


def test_optimizer_matches_exhaustive_lots_when_first_fill_precedes_payout():
    offers = [{"strike": str(i), "price": price, "depth": 12} for i, price in enumerate([19, 41])]
    samples = [(0, 1, 1), (1, 25, 18), (2, 21, 20)] * 5 + [(2, 32, 2)]
    plan = trip_cover.portfolio(offers, [0, 8, 26], fuel_cash=25, spend_limit=10, tail_samples=samples, first_fill_floor=True)
    def cash(lots):
        premium = sum(sum(trip_cover.cost(q, o)) for q, o in zip(lots, offers, strict=True))
        if premium > 10 or max(8 - lots[0], 26 - sum(lots)) > 25:
            return float("inf")
        needs = [max(first, peak-sum(lots[:band])) + premium for band, peak, first in samples]
        return fill_replay.tail_sum(needs)
    quantities = {leg["strike"]: leg["lots"] for leg in plan["legs"]}
    selected = [quantities.get(offer["strike"], 0) for offer in offers]
    assert cash(selected) == min(cash(pair) for pair in product(range(13), repeat=2))


def test_delaying_the_only_payout_preserves_the_first_stop_deficit():
    week = timedelta(days=7)
    first = date(2026, 2, 2)
    prices = {(first + i*week).isoformat(): level for i, level in enumerate([4000, 5000, 4300, 4200, 4100])}
    samples = [(1, 0, (first + week).isoformat())]
    trip = {"gallons": 10, "pump_mills": 4000, "budget_cents": 4000, "pump_cash_cents": 0}
    def path(payout, stop):
        return payday.paths(samples, trip, prices, 100, [0, payout], 4, first + 4*week, payout_stop=stop)[0][2]
    before = path(0, 1)
    for payout in [0, 75, 1000, 2200]:
        ready, late = path(payout, 1), path(payout, 2)
        assert late[0] == before[0]
        assert late[1:] == ready[1:]
        score = payday.hedge_cash([(1, max(before), before[0])], [0, payout], 21)
        assert score["late_cents"] == max(late) + 21
        assert score["on_time_cents"] == max(ready) + 21


def test_separate_price_periods_premium_and_cash_baseline():
    trip = dict(trip_cover.EXAMPLE, region="midwest", diesel_model="recent", pump_cash_cents=500, spend_limit_cents=300)
    target = trip_cover.analyze(trip_cover.recorded(), trip, reserve={"available_cents": 3002, "road_cents": 2500})["pump_target"]
    for days, count, premiums, costs in [(23, 22, [0, 300, 300], [34079, 33262, 33268]),
                                         (30, 18, [0, 300, 297], [26664, 26964, 26961])]:
        choice = target[f"payday_{days}"]
        report = choice["horizon"]
        rows = report["rows"]
        assert report["trained_through"] < "2025-01-01" < rows[0][3] and len(rows) == count
        assert all(a[4] < b[3] for a, b in zip(rows, rows[1:]))
        for name, premium, expected in zip(("cash", "original", "fitted"), premiums, costs, strict=True):
            score = report["scores"][name]
            assert score["premium_cents"] == premium
            assert score["late_cents"] == expected
            assert payday.hedge_cash(rows, score["payouts_by_band"], premium)["late_cents"] == expected
        assert report["starts"][0]["fitted"] == costs[-1]
        assert choice["after"]["short_cents"] <= target["after"]["short_cents"] + trip["pump_mills"] // 10
        assert target["backup_checks"][f"payday_{days}"]["payday"][days]["payout_stop"] == 2


def test_recorded_quote_cache_keeps_each_driver_budget_and_road_reserve_separate(monkeypatch):
    from freightos import app
    calls = []
    def analyze(snapshot, trip, *, reserve):
        calls.append((trip, reserve))
        return len(calls)
    monkeypatch.setattr(trip_cover, "analyze", analyze)
    app.recorded_trip.cache_clear()
    try:
        inputs = tuple(sorted(trip_cover.EXAMPLE.items()))
        assert app.recorded_trip(inputs, 2500) == app.recorded_trip(inputs, 2500) == 1
        assert app.recorded_trip(inputs, None) == 2
        assert app.recorded_trip(inputs, 0) == 3
        assert app.recorded_trip((*inputs, ("pump_cash_cents", 0)), 0) == 4
        assert calls[0][1]["road_cents"] == 2500 and calls[1][1] is None
    finally:
        app.recorded_trip.cache_clear()
