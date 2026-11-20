import gzip
import json
from copy import deepcopy
from random import Random

import pytest

from freightos import payday_policy as policy


def decide(budget, menu="both", fee=100, floor=0, stress=None, keep=False):
    return policy.decide(policy.quotes(menu), menu, fee, 0, 0, floor, stress, budget_cents=budget, keep_profile=keep)


def test_exact_price_envelope_agrees_with_exhaustive_oracle():
    random = Random(2030)
    for _ in range(2000):
        a,b,m = (random.randrange(0, 500), random.randrange(0, 500), random.randrange(1, 400))
        low = random.randrange(0, 100)
        high = low + random.randrange(0, 180)
        def gap(p):
            return -(-a*p//m) + (-b*p//m)
        maximum, price = policy.worst_rounding_gap(a,b,m,low,high)
        assert low <= price <= high and gap(price) == maximum == max(map(gap, range(low, high+1)))


def test_cannot_substitute_prices_across_index_change():
    events = json.loads(gzip.decompress(policy.HISTORY.read_bytes()))
    observed, omitted = policy.windows(events)
    assert (len(observed), len(omitted)) == (9, 4)
    changed = deepcopy(events)
    changed[max(events)]["contracts"][0]["settlementValue"] = "0"
    with pytest.raises(ValueError, match="outcomes"):
        policy.windows(changed)


def test_budget_and_customer_permissions_change_what_is_payable():
    result = decide(8000)
    plan = result["winner"]
    assert result["minimum_early_cents"] == plan["customer_cents"] == 4950
    assert (plan["customer_fee_cents"], plan["invoice_cents"], plan["least_cents"], plan["most_cents"]) == (50, 15000, 3887, 8746)
    assert plan["slot"] > 0 and plan["entry_cents"] <= 8000
    assert plan["cash_used_cents"] + plan["reserve_cents"] + plan["venue_cents"] + plan["customer_cents"] == 19000
    wait = next(p for p in result["plans"] if p["entry_lamports"] == result["wait_entry"])
    assert wait["entry_cents"] == 9820
    assert decide(wait["entry_cents"])["winner"]["customer_cents"] == 0
    assert decide(wait["entry_cents"]-1)["minimum_early_cents"] == 4950
    assert decide(5000)["minimum_early_cents"] == 9900
    assert decide(8000, "none")["winner"] is None
    assert decide(8000, floor=3888)["winner"] is None


def test_price_stress_and_global_regret():
    result = decide(8000, stress=15000)
    w = result["winner"]
    assert w["customer_cents"] == 4950 and w["regret_cents"] > 0
    affordable = [p for p in result["plans"] if not p["entry_gap"]]
    low = min(c["price_cents"] for c in result["cases"])
    def at(p, price):
        return p["post_refill_cents"] - (p["restore_lamports"] * price + 9999999) // 10000000
    for plan in affordable:
        if plan["payday_gap"] or plan["outside_stress"]:
            continue
        def loss(price):
            return max(at(r,price) for r in affordable if r.get("minimum_price", 0) <= price) - at(plan,price)
        assert loss(plan["regret_price"]) == plan["regret_cents"] == max(map(loss, range(low, 15001)))
    assert decide(8000, stress=16000)["winner"]["customer_cents"] == 9900
    assert all(p["outside_stress"] for p in decide(10000, stress=0)["plans"] if p["kind"] == "loan")


def test_matching_wallet_window_and_payday_profile():
    obs = policy.quotes("both")
    args = ("both", 100, 100000000, 0)
    original = policy.decide(obs,*args)
    keep = policy.decide(obs,*args,keep_profile=True)
    for p,q in zip(original["plans"], keep["plans"], strict=True):
        assert p["restore_lamports"] - q["restore_lamports"] == p.get("profile_lamports", 0)
    bad = [*obs[:-1], {**obs[-1], "entry_slot": original["first_slot"] + 241}]
    with pytest.raises(ValueError, match="window"):
        policy.decide(bad,*args)
    with pytest.raises(ValueError, match="menu"):
        policy.decide(obs,"half",100,100000000,0)


def test_fast_menu_matches_full_replay_of_every_devnet_observation():
    for customer, recorded in policy.recorded_menu().items():
        assert recorded == policy.fuel_swap.saved(customer)
