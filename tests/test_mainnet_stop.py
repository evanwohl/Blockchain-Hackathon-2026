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
    def unavailable(*args, **kwargs):
        raise ValueError("Unverified pool evidence")
    monkeypatch.setattr(mainnet_stop, "compare", unavailable)
    q = road_cash.evaluate(8000, 7500, 20000, 300, 15000)
    assert q["mainnet"] is None and q["separate_cents"] == 5432
    with pytest.raises(ValueError):
        road_cash.evaluate(8000, 7500, 20000, 300, 15000, mainnet_evidence=b"{}")


@pytest.mark.parametrize("menu,fee", [("partial", 0), ("partial", 990), ("partial", 10000), ("both", 100), ("none", 0)])
def test_early_freight_matches_exhaustive_cash_and_advance_pairs(menu, fee):
    bill, received, target, cash = 65, 69, 9, 38
    def price(outside):
        return (bill - outside + 3, 22 - (bill - outside) // 2 + outside % 4)
    offer = mainnet_stop.early_freight(bill, 0, received, target, cash, 40, price, menu, fee)
    allowed = range(bill + 1) if menu == "partial" else (0,)
    feasible, fees = [], []
    for early in allowed:
        charge = (early * fee + 9899) // 9900
        for own in range(bill - early + 1):
            opening, before_fee = price(early + own)
            if opening > 40:
                continue
            room = min(received - early, before_fee - target)
            if own <= cash and early and room >= 0:
                fees.append(room * 9900 // early)
            if charge <= room:
                feasible.append((early, own))
    eligible = [pair for pair in feasible if pair[1] <= cash]
    assert offer["minimum_cash_cents"] == min((own for _, own in feasible), default=None)
    for field, expected in (("smallest", min(eligible, default=None)),
                            ("keep_cash", min(eligible, key=lambda p: (p[1], p[0]), default=None))):
        row = offer[field]
        assert ((row["advance_cents"], row["cash_cents"]) if row else None) == expected
    assert offer["max_fee_per_99_cents"] == (None if eligible and min(eligible)[0] == 0 else max(fees, default=None))


def test_customer_offer_reconciles_each_cent_and_has_a_binding_negotiation_ceiling():
    def compare(menu="partial", fee=100, received=20000):
        return road_cash.evaluate(8000, 7500, received, 300, 15000, offer_menu=menu, offer_fee=fee)["mainnet"]["early_freight"]
    offer = compare()
    a, b = offer["smallest"], offer["keep_cash"]
    assert (offer["fuel_only"]["advance_cents"], offer["fuel_only"]["payday_cents"]) == (1544, -7559)
    assert (a["advance_cents"], a["fee_cents"], a["sale_cents"], a["payday_cents"]) == (8893,90,564,301)
    assert (b["advance_cents"], b["cash_cents"], b["fee_cents"], b["payday_cents"]) == (9900,6502,100,300)
    for row in (a,b):
        assert row["advance_cents"] + row["cash_cents"] + row["wallet_cents"] == 19000
        assert row["later_cents"] + row["advance_cents"] + row["fee_cents"] == 20000
    for menu, ceiling in (("partial",731),("both",699)):
        assert compare(menu)["max_fee_per_99_cents"] == ceiling
        assert compare(menu, ceiling)["smallest"] and compare(menu, ceiling+1)["smallest"] is None
    assert compare("both")["smallest"] == b
    assert compare("none")["smallest"] is None
    assert compare(received=15000)["smallest"] is None
    for menu, fee in (("wrong",10),("both",-1),("partial",False)):
        with pytest.raises(ValueError):
            compare(menu, fee)
