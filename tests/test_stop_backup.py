import gzip
import json
from random import Random

import pytest

from freightos import payday_policy, stop_backup


def test_exact_quote_boundary_matches_cent_search():
    random = Random(2026)
    for _ in range(200):
        costs = sorted(random.choices(range(5000), k=250))
        allowance = random.randrange(6000)
        assert stop_backup.boundary(len(costs), allowance, lambda n: costs[n-1]) == sum(c <= allowance for c in costs)


def test_paid_amount_and_neighbour_replay_for_both_pools():
    evidence = json.loads(gzip.decompress(stop_backup.SAMPLE.read_bytes()))
    q = stop_backup.replay(evidence)
    assert q == stop_backup.saved()
    assert q["covered_cents"] + q["outside_cents"] == q["bill_cents"] == 19000
    assert q["covered_cents"] == 15472 and q["protected_cents"] == 1058
    assert {r["pool"] for r in q["routes"] if r["pool"]} == set(map(str, stop_backup.fuel_swap.POOLS))
    for r in q["routes"]:
        assert r["wallet_raw"] + r["reserve_raw"] + r["swap_raw"] == r["vendor_raw"]
        assert r["retained_raw"] >= q["scale"] * q["protected_cents"]
        if r["pool"]:
            assert r["entry_lamports"] <= q["cap_lamports"] < r["next_cent_entry_lamports"]
    evidence["result"]["outside_cents"] -= 1
    with pytest.raises(ValueError, match="differs"):
        stop_backup.replay(evidence)


def test_payday_restores_outside_cash_and_the_full_authorized_sale_cost():
    q = stop_backup.saved()
    assert stop_backup.payday(q, [4984, 10735], 4300) == {"least_cents": 4233, "most_cents": 9695, "target_gap_cents": 67}
    external_only = {**q, "best": None, "outside_cents": 19000}
    assert stop_backup.payday(external_only, [0, 20000], 0)["least_cents"] == 1000
    assert payday_policy.decide(payday_policy.quotes("none"), "none", 100, 0, 0, budget_cents=8000)["recorded_backup"]
    assert not payday_policy.decide(payday_policy.quotes("none"), "none", 100, 0, 0, budget_cents=7999)["recorded_backup"]


def test_declined_pool_preserves_verified_alternative(monkeypatch):
    evidence = json.loads(gzip.decompress(stop_backup.SAMPLE.read_bytes()))
    del evidence["result"]
    original = stop_backup.pool_backup

    def decline_first(plan, pool, *args):
        row, slot = original(plan, pool, *args)
        if pool == stop_backup.fuel_swap.POOLS[0]:
            raise stop_backup.fuel_swap.PoolDeclined(slot)
        return row, slot

    monkeypatch.setattr(stop_backup, "pool_backup", decline_first)
    partial = stop_backup.replay(evidence)
    assert partial["covered_cents"] == 2970
    assert partial["outside_cents"] == 16030
    assert partial["unavailable_pools"] == [str(stop_backup.fuel_swap.POOLS[0])]
