from datetime import date, timedelta
from fractions import Fraction
from math import ceil

from freightos import payday_price

WEEK = timedelta(days=7)
FIRST = date(2026, 11, 23)


def paths(pattern, count=125, stops=4):
    end = FIRST - 2 * WEEK
    return [(*pattern(i), (end-(count-i+stops-2)*WEEK).isoformat(), (end-(count-1-i)*WEEK).isoformat())
            for i in range(count)]


def offers(price, fitted=3):
    return {"cash": {"premium_cents": 0, "payouts_by_band": [0, 0]},
            "original": {"premium_cents": price, "payouts_by_band": [0, 5]},
            "fitted": {"premium_cents": fitted, "payouts_by_band": [0, 10]}}


def test_integer_tie_and_irrecoverable_first_fill():
    rows = paths(lambda i: (1, 16, 6))
    result = payday_price.price_limit(rows, offers(5, 10), 4, FIRST)
    for mode in ("average", "hard"):
        assert result[mode] == {"choice": "cash", "max_price_cents": {"original": 4, "fitted": 9}}
    assert payday_price.price_limit(rows, offers(4, 9), 4, FIRST)["hard"]["choice"] == "original"
    assert payday_price.price_limit(rows, offers(4, 8), 4, FIRST)["average"]["choice"] == "fitted"
    lost = paths(lambda i: (1, 5, 5))
    assert payday_price.price_limit(lost, offers(0, 0), 4, FIRST)["hard"]["max_price_cents"]["fitted"] is None


def test_calendar_limits_against_independent_fractional_tail_calculation():
    rows = paths(lambda i: (int(i % 4 != 0), i % 17 + 10, i % 3))
    plans = offers(2)
    result = payday_price.price_limit(rows, plans, 4, FIRST)
    lookup = {r[3]: r for r in rows}
    def measure(values, mode):
        n = len(values)
        if mode == "average":
            return Fraction(sum(values), n)
        v = sorted(values, reverse=True)
        k, r = divmod(n, 10)
        return Fraction(10 * sum(v[:k]) + (r * v[k] if r else 0), n)
    for mode in ("average", "hard"):
        advantages = {name: [] for name in ("original", "fitted")}
        for dates in result["cohorts"]:
            selected = [lookup[d] for d in dates]
            assert all(a[4] < b[3] for a, b in zip(selected, selected[1:]))
            assert all(FIRST - 106 * WEEK <= date.fromisoformat(r[3]) <= date.fromisoformat(r[4]) <= FIRST - 2*WEEK for r in selected)
            for name in advantages:
                gross = measure([r[1] for r in selected], mode) - measure(
                    [max(r[2], r[1]-plans[name]["payouts_by_band"][r[0]]) for r in selected], mode)
                advantages[name].append(gross)
        caps = result[mode]["max_price_cents"]
        for name, scores in advantages.items():
            assert caps[name] == max((price for price in range(20) if all(price < x for x in scores)), default=None)
        picked = result[mode]["choice"]
        net = {name: min(scores) - plans[name]["premium_cents"] for name, scores in advantages.items()}
        net["cash"] = 0
        assert net[picked] == max(net.values())


def test_forecast_ignores_future_paths_and_suppresses_stale_or_thin_data():
    rows = paths(lambda i: (1, 100, 0))
    forecast = payday_price.price_limit(rows, offers(3), 4, FIRST)
    dirty = rows + [(0, 10000, 0, FIRST.isoformat(), (FIRST+3*WEEK).isoformat())]
    assert forecast == payday_price.price_limit(dirty, offers(3), 4, FIRST)
    assert payday_price.price_limit(rows[:-2], offers(3), 4, FIRST) is None
    assert payday_price.price_limit(rows[-30:], offers(3), 4, FIRST) is None
    replay = {"scores": offers(3), "rows": [rows[0], rows[-1]]}
    p = payday_price.price_policy(rows[:70], rows[70:], replay, 4, FIRST, FIRST + WEEK)
    assert p["current"] is None and len(p["records"]) == 1
    record = p["records"][0]
    assert record["first"] == rows[-1][3] and record["through"] <= (date.fromisoformat(record["first"])-2*WEEK).isoformat()
    assert record["average"]["choice"] == "fitted"
    assert p["summary"]["average"]["average_cents"] == 93


def test_exact_multi_leg_quote_targets_with_rounded_fees_and_cent_ticks():
    from freightos.trip_cover import basket_quote, cost
    legs = [{"strike": "81", "lots": 2, "price": 90},
            {"strike": "87", "lots": 157, "price": 8}]
    def all_in(cut):
        return sum(sum(cost(leg["lots"], {"price": max(1, ceil(Fraction(leg["price"] * (10000-cut), 10000)))}))
                   for leg in legs)
    by_discount = [all_in(cut) for cut in range(10001)]
    assert basket_quote(legs, None) is None
    for limit in range(22):
        target = basket_quote(legs, limit)
        first = next((i for i, price in enumerate(by_discount) if price <= limit), None)
        if first is None:
            assert target is None
        else:
            assert target["cut_bps"] == first
            assert target["all_in_cents"] == by_discount[first]
            assert sum(q["premium_cents"] + q["fee_cents"] for q in target["legs"]) == by_discount[first]
