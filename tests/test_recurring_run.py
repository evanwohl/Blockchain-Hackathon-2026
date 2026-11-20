from itertools import product

import pytest

from freightos import driver_desk, repeat_credit


STOP = dict(bill=18600, invoice=20000, cash=7500, budget=8000, stable=2043, target=300, stress=15000,
            offer_menu="counter", offer_amount=9000, offer_fee=300)


def test_returning_account_reopens_only_the_obligation_and_fourth_stop_changes_the_policy():
    returning = driver_desk.quote(dict(STOP, borrower_state="returning"))["mainnet"]["secured_loan"]
    assert returning["setup"]["retained_lamports"] == 0
    assert returning["setup"]["deposit_lamports"] == 17637760
    row = returning["combined"]["most_cash"]
    assert (row["payday_cents"], row["draw_cents"]) == (1094, 57)
    def series(**changes):
        return driver_desk.quote(dict(dict(STOP, borrower_state="new", stops=4), **changes))["series"]
    s = series()
    assert (s["choice"], s["open_cents"], s["debt_free_cents"], s["break_even_stops"], s["renewal_pct"]) == ("open", 4208, 4152, 4, 82)
    assert s["prices"] == [dict(price_cents=p, open_cents=b, debt_free_cents=f)
                           for p, b, f in ((4960, 4332, 4468), (7247, 4303, 4396), (15000, 4208, 4152))]
    assert s["later"]["payday_cents"] == row["payday_cents"]
    assert series(stops=3)["choice"] == "debt_free"
    tighter = series(target=1000)
    assert tighter["choice"] == "debt_free" and tighter["open_cents"] is None
    assert series(target=1100)["choice"] == "unfunded"


def test_repeated_decision_matches_all_opening_dates_including_unfunded_stops():
    def row(value):
        return None if value is None else {"payday_cents": value, "restore_lamports": 0}
    for first, returning, outside in product((None, 1, 5, 8), repeat=3):
        later = max((n for n in (returning, outside) if n is not None), default=None)
        for count in range(2, 6):
            s = driver_desk.recurring_plan(row(first), row(later), row(outside), count,
                    dict(floor_cents=2, low_price_cents=10, reference_cents=20, high_price_cents=30))
            schedules = []
            for elections in product((0, 1), repeat=count):
                opened = False
                cash = []
                for draw in elections:
                    cash.append((returning if opened else first) if draw else outside)
                    opened |= bool(draw)
                if all(c is not None and c >= 2 for c in cash):
                    schedules.append(sum(cash))
            got = max((c for c in (s["open_cents"], s["debt_free_cents"]) if c is not None), default=None)
            assert got == max(schedules, default=None)


@pytest.mark.parametrize("terms", [dict(stops=0), dict(stops=13), dict(stops=3), dict(borrower_state="other"),
                                  dict(borrower_state="returning", first_loan=True), dict(stops=True)])
def test_invalid_combinations_are_rejected(terms):
    with pytest.raises(ValueError):
        driver_desk.quote(dict(STOP, **terms))


def test_repeat_simulation_verifies_profile_vendor_funding_and_returned_deposit():
    import gzip
    import json
    proof = repeat_credit.saved()
    assert (proof["vendor_raw"], proof["repaid_raw"], proof["returned_rent"], proof["profile_lamports"]) == (5700, 5700, 17637760, 5892800)
    s = json.loads(gzip.decompress(repeat_credit.SAMPLE.read_bytes()))
    s["close"]["value"]["accounts"][2]["lamports"] += 1
    with pytest.raises(ValueError, match="profile"):
        repeat_credit.verify(s)
