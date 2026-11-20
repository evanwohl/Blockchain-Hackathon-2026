from itertools import product

import pytest

from freightos import driver_desk, route_booking
from freightos.mainnet_loan import payday_at

TERMS = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000,
             stress=15000, offer_menu="counter", offer_amount=25000, offer_fee=500, borrower_state="new", stops=4)


def test_dated_loan_beats_the_two_endpoint_rules_on_booked_route():
    series = driver_desk.quote(dict(TERMS, booked=2))["series"]
    assert series["choice"] == "defer"
    b = series["booking"]
    assert b["price_count"] == 11
    assert [(a["open_stop"], a["missed_cents"]) for a in b["alternatives"]] == [
        (None, 40504), (1, 12264), (2, 10168), (3, 20336), (4, 30504)]
    assert b["choice"]["witness"] == dict(stops=2, prices=[15000, 15000], rival_stop=1, cash_cents=459488)
    assert b["choice"]["open_stop"] == 2
    assert series["first"]["draw_cents"] == 9500


@pytest.mark.parametrize("booked", [0, 5, 1.5, True, None])
def test_invalid_booking_counts(booked):
    with pytest.raises(ValueError):
        driver_desk.quote(dict(TERMS, booked=booked))
    with pytest.raises(ValueError):
        driver_desk.quote(dict(TERMS, stops=1, booked=1))


def test_price_and_booking_envelope_matches_enumeration_including_witness():
    def row(balance, restore):
        return dict(payday_cents=balance, restore_lamports=restore)
    prices = [7, 14, 29]
    for rows in [(row(20, 400000000), row(23, 0), row(18, 800000000)),
                 (None, None, row(18, 0)), (row(20, 0), None, row(18, 0)),
                 (row(20, 0), row(22, 0), None), (None, None, None),
                 (row(1, 0), row(99, 0), row(18, 0)), (row(20, 0),) * 3]:
        for booked in (1, 3, 4):
            b = route_booking.compare(*rows, booked, 4, prices + [7], 5)
            def balance(opening, path):
                total = 0
                for i, price in enumerate(path, 1):
                    r = rows[2] if opening is None or i < opening else rows[0 if i == opening else 1]
                    if r is None or r["payday_cents"] < 5:
                        return None
                    total += payday_at(r, 29, price)
                return total
            dates = [d for d in (1, 2, 3, 4, None) if balance(d, [29] * 4) is not None]
            assert set(dates) == {a["open_stop"] for a in b["alternatives"]}
            for a in b["alternatives"]:
                d = a["open_stop"]
                worst = max(max(balance(r, path) for r in dates) - balance(d, path)
                            for n in range(booked, 5) for path in product(prices, repeat=n))
                assert worst == a["missed_cents"]
                w = a["witness"]
                assert w["cash_cents"] == balance(d, w["prices"])
                assert balance(w["rival_stop"], w["prices"]) == w["cash_cents"] + worst
            if dates:
                assert b["choice"] == min(b["alternatives"], key=lambda a: (a["missed_cents"], -(a["open_stop"] or 5)))
            else:
                assert b["choice"] is None
    assert route_booking.compare(*(row(20, 0),) * 3, 12, 12, [1], 5)["choice"]["open_stop"] is None
