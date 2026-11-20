from copy import deepcopy
from fractions import Fraction

import pytest

from freightos import freight_credit as credit


@pytest.mark.parametrize("drop", [20, 40, 60])
@pytest.mark.parametrize("days", [23, 30])
def test_saved_draw_has_a_matching_debt_payment_and_stress_buffer(drop, days):
    run = credit.saved(drop, days)
    q = credit.evaluate(run, drop, days)
    assert q["advance_raw"] == 1_900_000 and q["advance_cents"] == 19000
    assert q["cost_cents"] == (140 if days == 23 else 182)
    assert q["repay_cents"] - q["advance_cents"] == q["cost_cents"]
    assert q["minimum_lamports"] == q["collateral_lamports"]
    assert q["stress_ltv_bps"] <= (q["liquidation_pct"] - 10) * 100
    one_step_lower = Fraction((q["stress_ltv_bps"] - 1) * q["collateral_lamports"], q["collateral_lamports"] - credit.STEP)
    assert one_step_lower > (q["liquidation_pct"] - 10) * 100
    assert q["fee_lamports"] > 0 and q["setup_lamports"] > 0


def test_vendor_and_borrower_deltas_are_both_required():
    run = deepcopy(credit.saved(40, 23))
    balances = run["response"]["value"]["postTokenBalances"]
    vendor = next(row for row in balances if row["owner"] == "4gsggQruLRmysfwuT55zp9sbzaAV24maeZpt377xkUaC")
    vendor["uiTokenAmount"]["amount"] = str(int(vendor["uiTokenAmount"]["amount"]) - 1)
    with pytest.raises(ValueError, match="do not reconcile"):
        credit.evaluate(run, 40, 23)


def test_invalid_credit_scenarios_and_foreign_network_fail_closed():
    with pytest.raises(ValueError):
        credit.capture(100, 23)
    with pytest.raises(ValueError):
        credit.capture(40, 23, lambda *_: "another-chain")


@pytest.mark.parametrize(("gap", "cap", "full"), [(2430, 1000, True), (292, 1000, True), (2430, 100, False)])
def test_trip_credit_replays_exact_vendor_draw_within_pledge_cap(gap, cap, full):
    result = credit.trip_draw(gap, cap, 40, 23, recorded=True)
    loan = result["loan"]
    assert loan["advance_raw"] == loan["advance_cents"] * 100
    assert loan["advance_cents"] + result["remaining_cents"] == gap
    assert (result["remaining_cents"] == 0) == full
    assert loan["minimum_lamports"] <= loan["collateral_lamports"] <= cap * 10_000
    assert loan["stress_ltv_bps"] <= (loan["liquidation_pct"] - 10) * 100
    assert "fee_headroom_cents" not in loan
    assert loan["entry_cash_cents"] > loan["cost_cents"]
    parity = Fraction(loan["sol_price"]) * 100 * (loan["advance_cents"] + loan["entry_cash_cents"] + loan["cost_cents"]) / loan["advance_cents"]
    assert loan["buyback_break_even_cents"] - 1 < parity <= loan["buyback_break_even_cents"]
    if full:
        total = loan["entry_cash_cents"] + loan["cost_cents"]
        assert credit.compare_fee(result, None)["room_cents"] is None
        assert [credit.compare_fee(result, total + delta)["room_cents"] for delta in (-1, 0, 1)] == [-1, 0, 1]
    else:
        assert credit.compare_fee(result, 1000) is None


def test_unfunded_missing_and_mismatched_trip_proofs():
    def no_network(*args):
        pytest.fail("Unexpected network request")
    assert credit.trip_draw(292, 0, 40, 23, no_network)["remaining_cents"] == 292
    for gap, cap in ((True, 1), (0, 1), (100, -1), (100, True)):
        with pytest.raises(ValueError):
            credit.trip_draw(gap, cap, 40, 23, no_network)
    for gap, cap, days in ((293, 1000, 23), (292, 101, 23), (292, 100, 30)):
        with pytest.raises(ValueError, match="No saved check"):
            credit.trip_draw(gap, cap, 40, days, no_network, recorded=True)
    with pytest.raises(ValueError, match="do not reconcile"):
        credit.evaluate(credit.saved(40, 23), 40, 23, 29200)


def test_trip_draw_rechecks_liquidity_and_stress_after_resizing(monkeypatch):
    observations = []

    def simulate(collateral, rpc, min_slot=0, advance=None):
        observations.append((collateral, min_slot, advance))
        return {"collateral": collateral, "slot": len(observations), "advance": advance}

    def evaluate(observation, drop, days, advance, *, step):
        assert advance == observation["advance"] and (drop, days, step) == (40, 23, 1)
        cents = advance // 100
        required = cents * (1000 if cents == 100 else 1200)
        return {"advance_cents": cents, "minimum_lamports": required, "collateral_lamports": observation["collateral"],
                "remaining_liquidity_raw": (300 - cents) * 100, "slot": observation["slot"], "entry_cash_cents": 172,
                "cost_cents": 2, "sol_price": "72.92"}

    monkeypatch.setattr(credit, "simulate", simulate)
    monkeypatch.setattr(credit, "evaluate", evaluate)
    result = credit.trip_draw(1000, 32, 40, 23, lambda *_: credit.devnet.GENESIS)
    assert observations == [(1_000_000, 0, 10000), (300_000, 1, 30000), (320_000, 2, 26600)]
    assert result["remaining_cents"] == 734 and result["loan"]["pledge_millis"] == 32
    assert "estimated_full_pledge_millis" not in result


def test_partial_saved_loan_estimates_a_new_limit_without_changing_the_quote():
    q = credit.trip_draw(2430, 100, 40, 23, recorded=True)
    loan = q["loan"]
    estimate = q["estimated_full_pledge_millis"]
    assert loan["pledge_millis"] <= 100 < estimate
    step = credit.LAMPORTS // (1000 * loan["model_ratio"])
    required = loan["minimum_lamports"] * 2430
    assert (estimate-1) * loan["advance_cents"] * step < required <= estimate * loan["advance_cents"] * step
