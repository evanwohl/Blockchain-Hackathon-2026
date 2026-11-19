import gzip
import json
from decimal import Decimal

import pytest

from freightos import fuel_cover


def sample():
    return json.loads(gzip.decompress(fuel_cover.SNAPSHOT.read_bytes()))


def test_cash_deadline_changes_which_option_is_cheaper():
    result = fuel_cover.saved()
    p = result["position"]
    assert (result["cash_only_cents"], result["below_strike_cents"]) == (1051, 0)
    assert p == {"quantity": "10.50", "payout_cents": 1050, "premium_cents": 21, "fee_cents": 2,
                 "upfront_cents": 23, "residual_cents": 1, "usable_first_cents": 24, "fuel_first_cents": 1074}
    fuel = fuel_cover.fuel_update.saved()
    gaps = []
    for cents in range(7500, 9301):
        gap = fuel_cover.cash_gap(Decimal(cents) / 100, fuel)
        payout = p["payout_cents"] if cents >= 9000 else 0
        gaps.append(max(0, gap - payout))
    assert p["upfront_cents"] + max(gaps) == p["usable_first_cents"]


def test_expensive_second_price_level_and_limited_capacity_change_quantity_and_cost():
    snap = sample()
    snap["book"]["levels"]["asks"] = [{"price": ".02", "amount": "5"}, {"price": ".98", "amount": "8"}]
    p = fuel_cover.analyze(snap)["position"]
    assert 5 <= Decimal(p["quantity"]) <= Decimal("10.51")
    assert 24 < p["usable_first_cents"] <= 563
    snap["book"]["levels"]["asks"].pop()
    p = fuel_cover.analyze(snap)["position"]
    assert p["quantity"] == "5.00" and p["usable_first_cents"] == 562


@pytest.mark.parametrize("change", ["empty", "stale", "different ask", "expired"])
def test_an_unusable_quote_keeps_the_cash_only_budget(change):
    snap = sample()
    if change == "empty":
        snap["book"]["levels"]["asks"] = []
    elif change == "stale":
        snap["book"]["reported_at"] = "2026-11-19T22:00:00Z"
    elif change == "expired":
        snap["checked_at"] = "2026-11-20T22:00:00Z"
    else:
        snap["book"]["levels"]["asks"][0]["price"] = ".10"
    result = fuel_cover.analyze(snap)
    assert result["cash_only_cents"] == 1051 and result["position"] is None


@pytest.mark.parametrize("change", ["negative size", "wrong symbol", "off increment"])
def test_invalid_depth_cannot_back_a_position(change):
    snap = sample()
    if change == "wrong symbol":
        snap["book"]["symbol"] = "unrelated"
    else:
        snap["book"]["levels"]["asks"][0]["amount"] = "-10" if change == "negative size" else ".012"
    with pytest.raises(ValueError):
        fuel_cover.analyze(snap)
