import pytest

from freightos.funding import priced_plan


def calendar():
    return {
        "opening_cash_cents": 0,
        "reserve_floor_cents": 0,
        "bills": [{"id": "fuel", "day": 5, "cents": 74500}],
        "invoices": [{"id": "freight", "face_cents": 300000, "options": [
            {"name": "standard", "day": 28, "fee_bps": 0},
        ]}],
        "funding": [],
    }


@pytest.mark.parametrize("funding", [
    [],
    [{"id": "bank", "available_day": 6, "restore_day": 28,
      "net_capacity_cents": 80000, "fixed_cost_cents": 0, "term_cost_bps": 100}],
    [{"id": "bank", "available_day": 1, "restore_day": 10,
      "net_capacity_cents": 80000, "fixed_cost_cents": 0, "term_cost_bps": 100}],
])
def test_unfunded_bill_or_repayment_returns_actionable_error(funding):
    case = {**calendar(), "funding": funding}
    with pytest.raises(ValueError, match="No feasible funding plan"):
        priced_plan(case, "quick pay", "fuel budget", {}, {})


def test_elected_late_payment_cannot_silently_use_another_offer():
    case = calendar()
    case["invoices"][0]["options"].append(
        {"name": "early", "day": 1, "fee_bps": 500}
    )
    plan = priced_plan(case, "quick pay", "fuel budget", {}, {})
    assert plan["cost_cents"] == 15000
    assert plan["invoice_elections"][0]["option"] == "early"
    with pytest.raises(ValueError, match="No feasible funding plan"):
        priced_plan(case, "quick pay", "fuel budget", {}, {}, {"freight": "standard"})


def test_available_cash_covers_bill_without_creating_funding():
    case = {**calendar(), "opening_cash_cents": 75500, "reserve_floor_cents": 1000}
    plan = priced_plan(case, "cash on hand", "fuel budget", {}, {})
    assert plan["payment_cents"] == 0
    assert plan["other_funds_cents"] == 74500
    assert plan["cost_cents"] == 0
    assert plan["funding"] == []
