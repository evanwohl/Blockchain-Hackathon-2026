import gzip
from itertools import product
import json

import pytest

from freightos import atomic_fuel, driver_desk, mainnet_loan, route_booking, route_sizing, split_payment

TERMS = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000,
             stress=15000, offer_menu="counter", offer_amount=25000, offer_fee=500, borrower_state="new",
             stops=4, booked=2, balance=True)


def test_standing_loan_and_swap_pay_the_sample_gap_and_match_the_devnet_proof():
    series = driver_desk.quote(TERMS)["series"]
    choice, sizing = series["booking"]["choice"], series["sizing"]
    assert (choice["open_stop"], choice["missed_cents"], sizing["baseline_stop"], sizing["baseline_cents"]) == (1, 9436, 2, 19212)
    assert (choice["witness"]["cash_cents"], choice["witness"]["prices"]) == (942432, [4960] * 4)
    proof = split_payment.saved()
    for name in ("first", "later"):
        row = series[name]
        assert row["draw_cents"] + row["sale_cents"] + row["cash_cents"] + row["advance_cents"] + row["stable_cents"] == TERMS["bill"]
        assert (row["draw_cents"], row["repay_cents"], row["sale_cents"]) == (7298, 7362, 2202)
        assert sum(x["output_cents"] for x in row["swap"]["legs"]) == row["sale_cents"]
        assert (100 * row["sale_cents"], 100 * row["draw_cents"], row["pledge_lamports"] // 100) == (
            proof["sale_raw"], proof["loan_raw"], proof["pledge_lamports"])
    assert proof["vendor_raw"] == proof["loan_raw"] + proof["sale_raw"] == 950000
    assert 0 < proof["spent_lamports"] <= proof["max_lamports"] and proof["rollback_slot"] >= proof["slot"]


def test_exact_amounts_match_exhaustive_price_paths_and_hindsight_draws():
    prices, stops = [1700, 5300, 15000], 3
    fee, rent, setup = 5000, 2000000, mainnet_loan.borrower_setup()
    bill, invoice = 8, 1000
    plan = dict(bill_cents=bill, receipt_cents=invoice, invoice_cents=invoice, high_price_cents=max(prices))
    costs = [i * 260000 for i in range(bill + 1)]
    schedule = [(d * 750000, d + bool(d)) if d != 4 else None for d in range(bill + 1)]
    for floor, cap in [(0, 40000000), (700, 28000000), (0, 8000000), (0, 4300000)]:
        plan["floor_cents"] = floor
        def profiles(returning):
            rows = {}
            for d, terms in enumerate(schedule):
                if terms is None:
                    continue
                spend = 2 * mainnet_loan.LOAN_FEE_ALLOWANCE + setup["setup_fee_lamports"] + (0 if returning else setup["retained_lamports"])
                r = mainnet_loan.funding_row(plan, 0, 0, 0, 0, d, terms, costs, fee, rent, spend, setup["refunded_lamports"])
                if r["payday_cents"] >= floor and r["opening_lamports"] <= cap:
                    rows[d] = r
            return rows
        new, reuse = profiles(False), profiles(True)
        free = new.get(0)
        initial = max((r for d, r in new.items() if d), key=lambda r: r["payday_cents"], default=None)
        later = max(reuse.values(), key=lambda r: r["payday_cents"])
        for booked in (1, 3):
            b = route_booking.compare(initial, later, free, booked, stops, prices, floor)
            s = dict(first=initial, later=later, debt_free=free, stops=stops, booking=b)
            route_sizing.size(s, plan, prices, schedule, costs, 0, 0, cap, fee, rent, setup)
            def payout(r, p):
                return mainnet_loan.payday_at(r, max(prices), p)
            paths = [p for n in range(booked, stops + 1) for p in product(prices, repeat=n)]
            def play(draws):
                opened = False
                rows = []
                for d in draws:
                    r = (reuse if opened else new).get(d)
                    if r is None:
                        return None
                    rows.append(r)
                    opened |= bool(d)
                return rows
            def total(rows, path):
                return sum(payout(r, p) for r, p in zip(rows, path))
            hindsight = []
            for path in paths:
                feasible = (play(ds) for ds in product(range(bill + 1), repeat=len(path)))
                hindsight.append(max(total(rows, path) for rows in feasible if rows))
            for a in s["booking"]["alternatives"]:
                opening = a["open_stop"]
                options = [[free] * stops] if not opening else [
                    play([0] * (opening - 1) + [d] * (stops - opening + 1)) for d in range(1, bill + 1)]
                if opening and initial:
                    options.append([free] * (opening - 1) + [initial] + [later] * (stops - opening))
                expected = min(max(best - total(rows, path) for best, path in zip(hindsight, paths))
                               for rows in options if rows and all(rows))
                assert a["missed_cents"] == expected
            assert s["booking"]["choice"]["missed_cents"] == min(a["missed_cents"] for a in s["booking"]["alternatives"])


def test_simulation_requires_matching_message_token_deltas_and_failed_swap_rollback():
    def calls():
        return json.loads(gzip.decompress(split_payment.SAMPLE.read_bytes()))
    altered = calls()
    altered[-1]["params"][0] = altered[-2]["params"][0]
    with pytest.raises(ValueError, match="recorded transaction"):
        atomic_fuel.replay_calls(altered, split_payment.simulate)
    altered = calls()
    altered[-1]["response"]["value"]["postBalances"][0] += 1
    with pytest.raises(ValueError, match="roll back"):
        atomic_fuel.replay_calls(altered, split_payment.simulate)
    altered = calls()
    for token in altered[-2]["response"]["value"]["postTokenBalances"]:
        if token["mint"] == str(split_payment.devnet.MINT):
            token["uiTokenAmount"]["amount"] = str(1 + int(token["uiTokenAmount"]["amount"]))
    with pytest.raises(ValueError, match="reconcile"):
        atomic_fuel.replay_calls(altered, split_payment.simulate)


@pytest.mark.parametrize("changes", [dict(balance=1), dict(booked=None), dict(stops=1, booked=1)])
def test_invalid_amount_sizing_request(changes):
    with pytest.raises(ValueError):
        driver_desk.quote(dict(TERMS, **changes))
