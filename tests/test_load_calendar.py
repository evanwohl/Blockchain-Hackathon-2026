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


def test_confirmed_credit_and_early_offer_decisions():
    from freightos.load_calendar import funding_options

    case = dict(opening_date="2026-11-01", opening_cents=60000, floor_cents=10000,
                fuel_date="2026-11-05", gallon_hundredths=20000, pump_mills=3725,
                freight_cents=300000, freight_date="2026-11-28", freight_status="expected",
                surcharge_cents=18000, surcharge_date="2026-11-28", surcharge_status="expected",
                offer_fee_cents=500, offer_date="2026-11-05", use_offer=True,
                bank_cents=30000, bank_bps=100, bank_fixed_cents=0, bank_date="2026-11-05",
                bank_due="2026-11-28")

    def options(**changes):
        inputs = dict(case, **changes)
        return funding_options(inputs, project(inputs))

    assert options()["selected"]["gap_cents"] == 7000
    confirmed = options(use_bank=True)
    assert confirmed["selected"]["cost_cents"] == 570
    assert confirmed["waiting"]["cost_cents"] == 245
    assert options(use_bank=True, bank_date="2026-11-06")["selected"]["gap_cents"] == 7000
    assert options(use_bank=True, bank_due="2026-11-07")["selected"]["gap_cents"] == 7000
    assert options(use_bank=True, bank_cents=4000)["selected"]["gap_cents"] == 3000


def test_reserve_timing_uses_redeemable_limit_and_falls_back_to_bank():
    from freightos.load_calendar import funding_options

    inputs = dict(opening_date="2026-11-01", opening_cents=60000, floor_cents=10000,
                  fuel_date="2026-11-05", gallon_hundredths=20000, pump_mills=3725,
                  freight_cents=300000, freight_date="2026-11-28", freight_status="expected",
                  surcharge_cents=0, surcharge_status="unconfirmed", use_bank=True,
                  bank_cents=30000, bank_bps=500, bank_fixed_cents=0,
                  bank_date="2026-11-05", bank_due="2026-11-28",
                  reserve_date="2026-11-05", refill_date="2026-11-28")
    reserve = dict(capacity_cents=20000, fixed_cost_cents=200, term_cost_bps=26)
    def compare(**changes):
        case = dict(inputs, **changes)
        return funding_options(case, project(case), reserve)
    plan = compare()
    assert {d["source"]: d["net_proceeds_cents"] for d in plan["selected"]["funding"]} == {
        "Kamino reserve": 20000, "Approved bank line": 4500}
    assert plan["selected"]["cost_cents"] == 477
    assert compare(reserve_date="2026-11-06")["selected"] == plan["backup"]
    assert compare(use_bank=False)["selected"]["gap_cents"] == 4500
    assert compare(use_bank=False, reserve_date="2026-11-06")["selected"]["gap_cents"] == 24500
