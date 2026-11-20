from freightos.load_calendar import project


def test_receipt_date_changes_shortfall_and_fuel_rounds_to_cents():
    case = dict(opening_date="2026-11-01", opening_cents=60000, floor_cents=10000,
                fuel_date="2026-11-05", gallon_hundredths=20000, pump_mills=3725,
                freight_cents=300000, freight_date="2026-11-28", freight_status="expected",
                surcharge_cents=18000, surcharge_date="2026-11-06", surcharge_status="expected")
    assert project(case)["gap_cents"] == 24500
    assert project(dict(case, surcharge_date="2026-11-05"))["gap_cents"] == 6500
    early = project(dict(case, use_offer=True, offer_date="2026-11-05", offer_fee_cents=500))
    assert early["gap_cents"] == 7000
    assert early["rows"][-1]["balance_cents"] == 303000
    for status in ("received", "unconfirmed"):
        rows = project(dict(case, surcharge_status=status))["rows"]
        assert rows[-1]["balance_cents"] == 285500
    assert project(dict(case, gallon_hundredths=101))["fuel_cents"] == 376
