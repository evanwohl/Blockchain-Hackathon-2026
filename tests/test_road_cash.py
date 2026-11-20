import pytest

from freightos import fuel_floor, road_cash


def plan(budget=6500, cash=6000, received=20000, target=300):
    return road_cash.evaluate(budget, cash, received, target, 15000)


def test_both_repairs_clear_the_stop_and_payday_and_are_minimal():
    q = plan()
    assert q["status"] == "fuel_short" and q["fuel_gap_cents"] == 439
    assert q["wallet_cents"] + 6000 + q["fuel_gap_cents"] == 19000
    assert q["repairs"] == [{"input":"cash","amount_cents":6439,"increase_cents":439},
                            {"input":"budget","amount_cents":6727,"increase_cents":227}]
    for fix in q["repairs"]:
        amount = fix["amount_cents"]
        restored = plan(**{fix["input"]:amount})
        assert restored["status"] == "ready" and restored["low_payday_cents"] >= 300
        assert plan(**{fix["input"]:amount-1})["status"] == "fuel_short"
    assert plan(budget=6727)["separate_cents"] == 5999


def test_payday_requires_extra_cash_even_with_a_larger_wallet():
    q = plan(8000,5000)
    assert (q["separate_cents"], q["fuel_gap_cents"], q["payday_gap_cents"]) == (4992,0,27)
    assert q["repairs"] == [{"input":"cash","amount_cents":5432,"increase_cents":432}]
    assert plan(100000000,5000)["status"] == "payday_short"
    assert plan(8000,5431)["payday_gap_cents"] == 1
    assert plan(8000,5432)["payment_slot"] == 524267821
    late = plan(8000,7500,received=15000)
    assert late["repairs"] == [{"input":"received","amount_cents":19874,"increase_cents":4874}]
    assert plan(8000,7500,received=19873)["payday_gap_cents"] == 1
    assert plan(8000,7500,received=19874)["status"] == "ready"
    assert plan(0,0,received=0)["repairs"] == []


def test_projection_respects_network_fees_cash_only_and_saved_wallet():
    none = plan(0,0)
    assert none["fuel_gap_cents"] == 19000
    all_cash = plan(0,20000)
    assert all_cash["wallet_cents"] == all_cash["entry_lamports"] == 0
    assert all_cash["untouched_cents"] == all_cash["low_payday_cents"] == 1000
    q, native, rows = road_cash.recorded()
    assert native == 5940042102 and all(r.entry <= native for r in rows)
    assert plan(100000000,7500)["cap_lamports"] == native
    assert q["market"]["cap_lamports"] == fuel_floor.stop_backup.DEFAULT_CAP
    for args in [(False,0,0,0,None),(0,-1,0,0,None),(0,0,20001,0,None),(0,0,0,0,True)]:
        with pytest.raises(ValueError):
            road_cash.limits(*args)


def test_recovery_uses_the_full_wallet_but_never_requires_more_than_it_contains():
    q, native, rows = road_cash.recorded()
    reference = 7282
    for budget, cash, floor, high in ((3600,7500,0,10735),(6500,6400,0,15000),(5000,9000,500,14000),(0,19000,1500,17000)):
        result = road_cash.model(q,native,rows,budget,cash,20000,floor,reference,[4984,high])
        for fix in result["repairs"]:
            def viable(amount):
                # Recheck each candidate against the native spending limit and the cash calendar independently.
                cap = min(native,(amount if fix["input"] == "budget" else budget)*10000000//reference)
                cash_cap = amount if fix["input"] == "cash" else cash
                return any(p.separate <= cash_cap and p.entry <= cap and
                           (1000+p.exchange-floor)*10000000 >= p.restore*high for p in rows)
            assert viable(fix["amount_cents"])
            assert not viable(fix["amount_cents"]-1)
