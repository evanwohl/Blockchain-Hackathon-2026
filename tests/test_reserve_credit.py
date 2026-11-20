from copy import deepcopy
from decimal import Decimal

import pytest

from freightos import reserve_credit as routes


@pytest.mark.parametrize("drop", [20, 40, 60])
@pytest.mark.parametrize("days", [23, 30])
def test_split_funds_same_vendor_and_keeps_next_stop(drop, days):
    q = routes.saved(drop, days)
    assert q["reserve_cents"] + q["borrow_cents"] == q["bill_cents"] == 19000
    if days == 23:
        assert q["invoice_cents"] - q["projected_repay_cents"] - q["reserve_cents"] == 881
    assert q["keep_cents"] == q["holdback"]["cost_cents"] == 1058
    assert q["split_pledge_millis"] < q["full_pledge_millis"]
    assert 0 < q["same_rate_saved_cents"] <= q["saved_cents"]
    assert q["baseline_cost_cents"] - q["split_cost_cents"] == q["saved_cents"]
    reserve = routes.snapshot()["reserve_result"]
    redeemed = Decimal(reserve["vendor_raw"] + reserve["wallet_increase_raw"]) / reserve["raw_units_per_cent"]
    for extra in (0, 1):
        rate = Decimal(q["max_yield_bps"] + extra) / 10000
        carry = routes.credit.ceiling(redeemed * ((1 + rate) ** (Decimal(days) / 365) - 1))
        assert (q["saved_cents"] + q["missed_yield_cents"] > carry) == (extra == 0)


def test_replay_reserve_matches_displayed_snapshot():
    data = routes.snapshot()
    assert routes.replay_reserve(data["reserve"]) == data["reserve_result"]
    with pytest.raises(ValueError, match="differs from the saved instructions"):
        routes.replay_reserve(data["reserve"][1:])


def test_reserve_or_loan_evidence_cannot_switch_amount_or_vendor():
    data = routes.snapshot()
    loans = deepcopy(data["loans"]["40-23"])
    reserve = data["reserve_result"]
    for key in ("vendor_raw", "retained_raw", "shortfall_raw"):
        with pytest.raises(ValueError, match="do not match"):
            routes.compare({**reserve, key: 0}, loans, 40, 23)
    loans[1]["wire"] = loans[0]["wire"]
    with pytest.raises(ValueError, match="simulated transaction"):
        routes.compare(reserve, loans, 40, 23)
