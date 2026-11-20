import gzip
from fractions import Fraction
import json

import pytest

from freightos import atomic_fuel, collateral_exit, driver_desk, flash_exit, fuel_floor, mainnet_stop, payoff_budget, stop_backup


def test_raw_output_router_matches_every_legal_split_including_tick_limits_and_rounding():
    def pool(price, cap, fee, liquidity):
        return dict(pool=str((price, fee)), price=fuel_floor.Q64*price//100, max_raw=cap, fee=fee, liquidity=liquidity)
    cases = [(pool(34, 71, 400, 2200), pool(35, 97, 3000, 2100)),
             (pool(35, 81, 500, 3500), pool(35, 79, 500, 3600)),
             (pool(27, 2, 400, 10**15), pool(26, 40, 3000, 10**15))]
    for pools in cases:
        for n in range(0, sum(p["max_raw"] for p in pools)+2):
            plans = [(stop_backup.buffered(fuel_floor.swap_cost(pools[0], n-k)) +
                      stop_backup.buffered(fuel_floor.swap_cost(pools[1], k)))
                     for k in range(n+1) if n-k <= pools[0]["max_raw"] and k <= pools[1]["max_raw"]]
            result = payoff_budget.raw_sale(pools, n)
            if plans:
                assert result["max_lamports"] == min(plans)
                assert sum(x["output_raw"] for x in result["legs"]) == n
            else:
                assert result is None


def test_cash_shortfall_disabled_flash_and_full_repayment():
    _, pools = mainnet_stop.saved()
    row = dict(repay_cents=200, draw_cents=195, pledge_lamports=18_000_000)
    lending = dict(flash_fee_sf=1, flash_available_raw=100_000_000, origination=0)
    for fee in (1, collateral_exit.FLASH_DISABLED):
        terms = dict(lending, flash_fee_sf=fee)
        for cash in (0, 7, 72, 199, 200, 250):
            q = payoff_budget.quote(row, cash, pools, terms, [4960, 7247])
            feasible = []
            for draw in range(max(0,200-cash)+1):
                if draw and fee == collateral_exit.FLASH_DISABLED: continue
                raw = draw*10000 + (1 if draw else 0)
                if payoff_budget.raw_sale(pools, raw)["max_lamports"] <= row["pledge_lamports"]:
                    feasible.append(draw)
            assert q["credit_cents"] == max(feasible)
            assert q["cash_required_cents"] + q["credit_cents"] == 200
            assert q["short_cents"] == max(0, q["cash_required_cents"]-cash)
            assert q["unused_cents"] == max(0, cash-200)


def test_sample_budget_is_bound_to_the_selected_route_and_independent_price_hurdle():
    terms = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000,
                 stress=15000, offer_menu="counter", offer_amount=25000, offer_fee=500, borrower_state="new", stops=4, booked=2, balance=True)
    s = driver_desk.quote(dict(terms, payoff_cash=3000))["series"]
    for r in (s["first"], s["later"]):
        q = r["cash_payoff"]
        assert (q["credit_cents"], q["output_raw"], q["fee_cap_raw"], q["min_left_lamports"]) == (4362,43620437,437,1587264719)
        assert q["cash_required_cents"] == 3000 and q["price_for_three_quarters_cents"] == 7960
        assert (q["prices_below"], q["price_count"]) == (4,11)
        exact = Fraction(q["output_raw"]*10**9, 10000*(r["pledge_lamports"]//4))
        assert 7959 < exact <= 7960
    assert q["three_quarter_cash_cents"] == 3410
    for budget in (3409, 3410):
        r = driver_desk.quote(dict(terms, payoff_cash=budget))["series"]["first"]
        assert (4 * r["cash_payoff"]["min_left_lamports"] >= 3 * r["pledge_lamports"]) == (budget == 3410)
    for value in (-1, None, 0.5, True):
        with pytest.raises(ValueError):
            driver_desk.quote(dict(terms, payoff_cash=value))


def test_cash_contribution_and_flash_payoff_reconcile_on_devnet():
    p = flash_exit.saved_mixed()
    assert p["cash_contributed_raw"] + p["sale_raw"] == p["repaid_raw"] == 729800
    assert p["bridge_raw"] == 429801 and p["remaining_usdc_raw"] == 0 and p["retained_lamports"] == 18896639
    assert p["rollback_slot"] >= p["slot"]
    calls = json.loads(gzip.decompress(flash_exit.MIXED_SAMPLE.read_bytes()))
    with pytest.raises(ValueError, match="recorded transaction"):
        atomic_fuel.replay_calls(calls, lambda rpc: flash_exit.simulate(rpc, cash_raw=299999))
