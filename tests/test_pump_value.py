from datetime import date, timedelta
from fractions import Fraction

from freightos import forward, pump_value


def test_frozen_cash_ceiling_and_validation_reconcile_per_week():
    result = forward.published_value()
    assert (result["weeks"], result["short_weeks"], result["paid_weeks"], result["helped_weeks"]) == (86, 19, 23, 8)
    assert (result["mean_ceiling_cents"], result["tail_ceiling_cents"], result["premium_cents"]) == (22, 94, 95)
    assert result["single_quote"] == {"ask": 33, "strike": "84", "mean_limit": 7, "tail_limit": 32}
    records = result["weekly_checks"]
    assert len(records) == 41
    validation = result["validation"]
    for model in ("local", "pooled"):
        buys = [row for row in records if row[model + "_buys"]]
        assert len(buys) == validation[model]["buys"] == 0
        assert sum(row["covered_cents"] - 95 for row in buys) == validation[model]["net_relief_cents"]
    assert sum(row["covered_cents"] - 95 for row in records) == validation["always_net_cents"] == -2005
    for row in records:
        assert date.fromisoformat(row["training_through"]) <= date.fromisoformat(row["fill"]) - timedelta(days=14)
    assert result["estimate"]["local_cents"] == 7
    assert result["estimate"]["pooled_cents"] == 22


def test_neighborhood_respects_ties_and_shrinks_toward_the_pooled_mean():
    history = [(1, 0), (2, 0), (2, 4), (3, 8), (9, 33)]
    local, pooled = pump_value.predict(history, 2)
    assert pooled == 9 and local == Fraction(40, 6)
    assert pump_value.predict(history[::-1], 2) == (local, pooled)


def test_new_or_unreleased_prices_cannot_rewrite_an_earlier_forecast():
    week = timedelta(days=7)
    start = date(2025, 2, 3)
    weeks = [start + i * week for i in range(72)]
    prices = {day.isoformat(): 3500 + i**2 % 167 for i, day in enumerate(weeks)}
    plan = {"upfront_cents": 4, "legs": [{"strike": "84", "lots": 20}]}
    offers = [{"strike": "84"}]
    samples = [(1, i % 17, day.isoformat()) for i, day in enumerate(weeks)]
    def run(rows, tape):
        return pump_value.evaluate(rows, offers, plan, tape, weeks[-1] + 2 * week, weeks[-1] + week)
    fixed = run(samples, prices)
    day = date.fromisoformat(fixed["weekly_checks"][0]["fill"])
    cutoff = day - 2 * week
    changed = run([(1, need if stamp <= cutoff.isoformat() else 0, stamp) for _, need, stamp in samples],
                  {stamp: price if stamp <= cutoff.isoformat() else 9000 for stamp, price in prices.items()})
    original, amended = (r["weekly_checks"][0] for r in (fixed, changed))
    for key in ("local_ceiling_cents", "pooled_ceiling_cents", "training_through", "local_buys", "pooled_buys"):
        assert original[key] == amended[key]
    assert fixed["estimate"]
    assert "estimate" not in run(samples[:-1], prices)
    assert "estimate" not in run(samples, {d: p for d, p in prices.items() if d != weeks[-2].isoformat()})
    assert pump_value.evaluate(samples, offers, {"upfront_cents": 0, "legs": []}, prices, weeks[-1], day) is None


def test_quote_limits_allow_no_tick_and_do_not_invent_a_price_for_multiple_strikes():
    from freightos import trip_cover
    day = date(2026, 1, 5)
    offers = [{"strike": "83", "price": 30, "lots": 120}, {"strike": "84", "price": 18, "lots": 240}]
    def replay(legs):
        return trip_cover.pump_price_check([(0, 0, day.isoformat())], offers, {"legs": legs, "upfront_cents": 37},
                                           {"region": "midwest"}, day, day)
    assert replay(offers[:1])["single_quote"]["mean_limit"] is None
    assert "single_quote" not in replay(offers)
