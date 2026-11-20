import copy

import pytest

from freightos import fuel_floor, mainnet_stop, road_cash, stop_backup


def test_two_pool_routing_matches_every_whole_cent_allocation_including_partial_ranges():
    unit = fuel_floor.Q64
    pools = [dict(price=unit, liquidity=900000, fee=3000, max_raw=600000),
             dict(price=unit, liquidity=1400000, fee=400, max_raw=900000)]
    costs, second = mainnet_stop.split_costs(pools, 150)
    for total in range(151):
        candidates = {leg: sum(stop_backup.buffered(fuel_floor.swap_cost(pool, cents * 10000))
                               for pool, cents in zip(pools, (total-leg, leg)))
                      for leg in range(max(0, total-60), min(90, total)+1)}
        assert costs[total] == min(candidates.values()) == candidates[second[total]]
    assert 0 < second[35] < 35


def test_mainnet_reprices_full_size_fuel_and_replacement_under_identical_terms():
    def quote(cash, budget=8000, received=20000):
        return road_cash.evaluate(budget, cash, received, 300, 15000)["mainnet"]
    m = quote(7500)
    assert (m["slot"], m["spot_cents"]) == (471652114, [7252, 7263])
    assert m["covers_full_range"] and m["first_cash_cents"] == 9044
    assert m["target_cash_cents"] == 16309 and m["target_sale_cents"] == 648
    assert m["original"]["wallet_limit_cents"] == 11646 and not m["original"]["wallet_fits"]
    assert m["original"]["sale_cents"] == 11525
    assert m["fuel_short_cents"] == 1544
    assert quote(9043)["fuel_short_cents"] == 1 and quote(9044)["fuel_short_cents"] == 0
    assert quote(16308)["payday_gap_cents"] == 1
    assert quote(16309)["target_plan_payday_cents"] == 300 and quote(16309)["payday_gap_cents"] is None
    assert quote(19000, 0)["original"]["opening_lamports"] == 0
    assert quote(19000, 0, 0)["target_cash_cents"] is None


def test_mainnet_evidence_pins_accounts_token_pair_and_cluster():
    raw = mainnet_stop.EVIDENCE.read_bytes()
    with pytest.raises(ValueError):
        mainnet_stop.verify(raw + b" ")
    data, _ = mainnet_stop.verify(raw)
    for field, value in (("genesis","devnet"), ("addresses",list(reversed(data["addresses"])))):
        with pytest.raises(ValueError):
            mainnet_stop.curves({**data, field:value})
    changed = copy.deepcopy(data)
    changed["accounts"]["value"][0]["executable"] = True
    with pytest.raises(ValueError):
        mainnet_stop.curves(changed)


def test_missing_mainnet_observation_keeps_the_devnet_plan_available(monkeypatch):
    def unavailable(*args):
        raise ValueError("Unverified pool evidence")
    monkeypatch.setattr(mainnet_stop, "compare", unavailable)
    q = road_cash.evaluate(8000, 7500, 20000, 300, 15000)
    assert q["mainnet"] is None and q["separate_cents"] == 5432
    with pytest.raises(ValueError):
        road_cash.evaluate(8000, 7500, 20000, 300, 15000, mainnet_evidence=b"{}")
