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
