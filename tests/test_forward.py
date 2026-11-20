from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest

from freightos import forward, fuel_update, trip_cover


def test_published_plans_fit_the_open_books_and_funding_slot():
    t, funding = forward.saved(), forward.funding()
    snap = t["quotes"]
    market = fuel_update.compare(snap["market"])
    offers = {o["symbol"]: o for o in trip_cover.top_offers(snap, market, trip_cover.contracts(snap["market"]))}
    assert datetime.fromisoformat(snap["checked_at"]) <= datetime.fromisoformat(t["recorded_at"]) < datetime.fromisoformat(t["expiry"])
    assert t["reserve"]["slot"] == t["vault"]["slot"]
    assert t["reserve"]["available_cents"] == t["vault"]["return_raw"] // t["vault"]["raw_units_per_cent"]
    assert market["status"] == "active" and market["target_ticker"] == t["event"]
    for plan in t["plans"].values():
        cost = 0
        for leg in plan["legs"]:
            quote = offers[leg["symbol"]]
            assert (quote["strike"], quote["price"]) == (leg["strike"], leg["price"])
            assert 0 < leg["lots"] <= quote["depth"]
            cost += sum(trip_cover.cost(leg["lots"], quote))
        assert cost == plan["upfront_cents"] <= t["trip"]["spend_limit_cents"]
    assert funding["targets"][t["primary"]] == funding["credit"]["gap_cents"] == 3298
    assert funding["credit"]["loan"]["slot"] >= t["reserve"]["slot"]


def test_scores_wait_independently_then_use_exact_strike_and_cash_shortfall():
    ticket, capacity = forward.saved(), forward.funding()
    def row(oil=None, diesel=None):
        result = forward.score(ticket, capacity, {"close": oil} if oil else None, {"mills": diesel} if diesel else None)
        return next(r for r in result["plans"] if r["key"] == ticket["primary"])
    assert row()["extra_cash_cents"] is None
    assert row("84")["short_cents"] is None
    assert row(diesel=6500)["short_cents"] is None
    data = row("84", 6500)
    assert data["payout_cents"] == 270
    assert data["short_cents"] == 65000 - 59670 - 502 - 500 - 270
    assert data["extra_cash_cents"] == 4153
    assert data["after_credit_cents"] == data["short_cents"] - 2812
    assert row("83.999", 6500)["payout_cents"] == 0
    assert row("84", 5000)["extra_cash_cents"] == 95


def test_eia_verifies_series_exact_date_and_vintage():
    day = date(2026, 11, 23)
    html = b"Weekly Midwest No 2 Diesel Retail Prices<tr><td>2026-Nov</td><td>11/16</td><td>5.967</td><td>11/23</td><td>6.100</td></tr><td>Release Date: 11/24/2026</td>"
    assert forward.diesel_observation(html, day, day + timedelta(days=1))["mills"] == 6100
    assert forward.diesel_observation(html, day + timedelta(days=7), day + timedelta(days=2)) is None
    for wrong in (html.replace(b"Midwest", b"East"), html.replace(b"11/23", b"11/16"), html.replace(b"6.100", b"NA")):
        with pytest.raises(ValueError):
            forward.diesel_observation(wrong, day, day + timedelta(days=1))
    with pytest.raises(ValueError):
        forward.diesel_observation(html, day, day)


def test_settlement_uses_frozen_event_and_official_resolution():
    t = forward.saved()
    observation = deepcopy(t["quotes"]["market"]["events"][t["event"]])
    expiry = datetime.fromisoformat(t["expiry"])
    assert forward.settlement(t, observation, expiry) is None
    event = observation["event"]
    stamp = (expiry + timedelta(seconds=40)).isoformat()
    observation["reported_at"] = stamp
    event.update(status="settled", resolvedAt=stamp, settlement={"value": "84"})
    for leg in event["contracts"]:
        leg.update(status="settled", resolvedAt=stamp, settlementValue="84",
                   resolutionSide="yes" if float(leg["strike"]["value"]) <= 84 else "no")
    assert forward.settlement(t, observation, datetime.fromisoformat(stamp))["close"] == "84"
    event["sourceDetails"]["index"] = "wrong"
    with pytest.raises(ValueError):
        forward.settlement(t, observation, datetime.fromisoformat(stamp))


def test_first_profitable_pump_tick_and_oil_threshold():
    ticket, funding = forward.saved(), forward.funding()
    edge = forward.score(ticket, funding)["edge"]
    assert edge == {"minimum_strike": "84", "minimum_pump_mills": 6077, "maximum_saving_cents": 175}
    for oil, pump, difference in [("83.99", 6100, -95), ("84", 6076, -7), ("84", 6077, 3), ("84", 6500, 175)]:
        plans = {p["key"]: p for p in forward.score(ticket, funding, {"close": oil}, {"mills": pump})["plans"]}
        assert plans["cash"]["extra_cash_cents"] - plans[ticket["primary"]]["extra_cash_cents"] == difference
