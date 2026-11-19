from itertools import product

from hedging.quick_pay import price_calendar


def payment(name, day, fee_bps):
    return {"name": name, "day": day, "fee_bps": fee_bps}


def case():
    return {
        "opening_cash_cents": 0,
        "reserve_floor_cents": 0,
        "bills": [{"id": "fuel", "day": 2, "cents": 70000}],
        "invoices": [
            {
                "id": "load",
                "face_cents": 300000,
                "options": [
                    payment("standard", 28, 0),
                    payment("quick", 1, 500),
                    payment("seven", 7, 300),
                ],
            }
        ],
        "funding": [],
    }


def test_whole_invoice_and_due_date():
    c = case()
    assert price_calendar(c)["cost_cents"] == 15000
    c["bills"][0]["day"] = 7
    assert price_calendar(c)["cost_cents"] == 9000
    c["bills"][0]["day"] = 28
    assert price_calendar(c)["cost_cents"] == 0


def test_smallest_invoice_and_partial_draw_can_win_together():
    c = case()
    c["invoices"].append(
        {
            "id": "backhaul",
            "face_cents": 20000,
            "options": [payment("standard", 28, 0), payment("quick", 1, 500)],
        }
    )
    c["funding"] = [
        {
            "id": "reserve",
            "available_day": 2,
            "restore_day": 28,
            "net_capacity_cents": 52000,
            "fixed_cost_cents": 100,
            "term_cost_bps": 40,
        }
    ]
    r = price_calendar(c)
    assert r["funding"][0]["net_proceeds_cents"] == 51000
    assert r["cost_cents"] == 1304
    assert r["cash_after_settlement_cents"] == 320000 - 70000 - 1304
    c["funding"][0]["available_day"] = 3
    assert price_calendar(c)["cost_cents"] == 15000


def test_no_cash_manufactured_or_borrowing_past_repayment_date():
    c = case()
    c["invoices"][0]["options"] = [payment("standard", 28, 0)]
    c["funding"] = [
        {
            "id": "credit",
            "available_day": 1,
            "restore_day": 5,
            "net_capacity_cents": 80000,
            "fixed_cost_cents": 1,
            "term_cost_bps": 20,
        }
    ]
    assert price_calendar(c) == {"status": "unfunded"}
    c["opening_cash_cents"] = 71000
    c["reserve_floor_cents"] = 1000
    assert price_calendar(c)["funding"] == []


def test_solver_matches_exhaustive_small_cent_book():
    c = case()
    c["invoices"][0].update(
        face_cents=35, options=[payment("regular", 4, 0), payment("early", 1, 1000)]
    )
    c["funding"] = [
        {
            "id": str(i),
            "available_day": 1,
            "restore_day": 4,
            "net_capacity_cents": 12,
            "fixed_cost_cents": i,
            "term_cost_bps": bps,
        }
        for i, bps in [(0, 1350), (1, 750)]
    ]
    for bill in range(1, 36):
        c["bills"][0]["cents"] = bill
        costs = [
            fee + (a * 1350 + 9999) // 10000 + int(b > 0) + (b * 750 + 9999) // 10000
            for early, a, b in product([False, True], range(13), range(13))
            for fee in [4 if early else 0]
            if (31 if early else 0) + a + b >= bill
        ]
        feasible = [x for x in costs if 35 - bill - x >= 0]
        r = price_calendar(c)
        assert r.get("cost_cents") == min(feasible, default=None)


def test_one_election_must_survive_late_funding_and_higher_fuel_cost():
    c = case()
    c["bills"][0]["max_cents"] = 73000
    c["funding"] = [
        {
            "id": "reserve",
            "available_day": 1,
            "latest_available_day": 3,
            "restore_day": 28,
            "net_capacity_cents": 80000,
            "fixed_cost_cents": 0,
            "term_cost_bps": 0,
        }
    ]
    result = price_calendar(c)
    assert result["payments"][0]["option"] == "quick"
    assert result["balances_cents"][2] == 285000 - 73000


def test_broker_wide_election_cannot_accelerate_just_small_invoice():
    c = case()
    extra = dict(c["invoices"][0], id="small", face_cents=80000)
    c["invoices"].append(extra)
    assert price_calendar(c)["cost_cents"] == 4000
    for inv in c["invoices"]:
        inv["election_group"] = "enrolled broker"
    assert price_calendar(c)["cost_cents"] == 19000


def test_minimum_useful_capacity_includes_invoice_discount_and_deadline():
    from hedging.quick_pay import explain_calendar

    c = case()
    c["invoices"].append(
        {
            "id": "small",
            "face_cents": 20000,
            "options": [payment("standard", 28, 0), payment("quick", 1, 500)],
        }
    )
    c["funding"] = [
        {
            "id": "reserve",
            "available_day": 2,
            "restore_day": 28,
            "net_capacity_cents": 60000,
            "fixed_cost_cents": 100,
            "term_cost_bps": 40,
        }
    ]
    alt = explain_calendar(c)["alternatives"][0]
    assert alt["minimum_useful_capacity_cents"] == 51000
    assert alt["cost_at_threshold_cents"] == 1304
    assert alt["cost_one_cent_below_cents"] == 15000
