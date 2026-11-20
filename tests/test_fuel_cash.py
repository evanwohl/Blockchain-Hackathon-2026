import gzip
import json
from random import Random

from freightos import fuel_cash, stop_backup


def test_both_splits_match_the_public_payments_and_driver_limits():
    data = json.loads(gzip.decompress(fuel_cash.SAMPLE.read_bytes()))
    result = stop_backup.replay(data)
    assert result == fuel_cash.saved()
    assert result["last_slot"] - result["first_slot"] <= 240
    assert result["cap_lamports"] == stop_backup.DEFAULT_CAP
    for q in [result["best"], *result["fixed_routes"]]:
        assert q["entry_lamports"] <= result["cap_lamports"]
        assert q["retained_raw"] >= result["protected_cents"] * result["scale"]
        assert q["vendor_raw"] == q["reserve_raw"] + q["wallet_raw"] + q["swap_raw"]
    choices = fuel_cash.compare(result, [4984, 10735])
    assert choices["lasting_price_cents"] == 14136
    a, b = choices["choices"]
    assert (a["separate_cents"], a["untouched_cents"], a["least_cents"], a["regret_cents"]) == (3528, 3972, 4233, 0)
    assert (b["separate_cents"], b["vendor_cents"], b["least_cents"], b["most_cents"]) == (7500, 11500, 3278, 7124)
    assert a["restore_lamports"] > b["restore_lamports"]
    assert a["minimum_edge_cents"] == 955
    assert b["regret_cents"] == 2571
    stressed = fuel_cash.compare(result, [4984, 15000])["choices"]
    assert [p["least_cents"] for p in stressed] == [182, 426]
    assert [p["separate_cents"] for p in stressed if p["least_cents"] >= 300] == [7500]


def test_crossover_counts_rounding_ties_and_reversals():
    random = Random(391)
    for _ in range(500):
        m = random.randint(3, 40)
        b = random.randint(0, 70)
        a = b + random.randint(1, 30)
        first = {"principal_cents": random.randint(0, 20), "restore_lamports": a}
        second = {"principal_cents": random.randint(0, 20), "restore_lamports": b}
        def gap(price):
            return fuel_cash.cash_left(first, price, m) - fuel_cash.cash_left(second, price, m)
        switch = fuel_cash.lasting_advantage(first, second, m)
        assert switch == 1 + max((p for p in range(1000) if gap(p) >= 0), default=-1)
        actual, witness = fuel_cash.advantage(first, second, m, 11, 300)
        assert actual == gap(witness) == max(map(gap, range(11, 301)))


def test_insufficient_external_and_all_external_funds():
    q = fuel_cash.saved()
    base = {**q, "fixed_routes": []}
    assert not fuel_cash.compare({**base, "separate_cents": 3527}, [4984, 10735])["choices"]
    assert len(fuel_cash.compare({**base, "separate_cents": 3528}, [4984, 10735])["choices"]) == 1
    all_cash = fuel_cash.compare({**base, "separate_cents": 20000}, [4984, 10735])["choices"][1]
    assert (all_cash["wallet_cash_cents"], all_cash["restore_lamports"], all_cash["vendor_cents"]) == (0, 0, 0)
    assert all_cash["untouched_cents"] == all_cash["least_cents"] == 1000
