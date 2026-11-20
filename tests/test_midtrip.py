import base64
import gzip
import json
from decimal import Decimal
from fractions import Fraction
from math import ceil, floor

import pytest

from freightos import atomic_fuel, collateral_path, driver_desk, mainnet_loan, midtrip, partial_payment


def route(budget, price):
    return driver_desk.quote(dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000,
                                  stress=15000, offer_menu="counter", offer_amount=25000, offer_fee=500, borrower_state="new",
                                  stops=4, booked=2, balance=True, payoff_cash=budget, midtrip_price=price))["series"]["first"]


def test_minimum_payment_matches_every_gemini_checkpoint_and_one_cent_less_fails():
    row = route(3000, 5500)
    q = row["cash_payoff"]["midtrip"]
    assert (q["needed_cents"], q["unused_cents"], q["payday_cents"]) == (1068, 1932, 6289)
    assert (q["windows"], q["binding_hours"], q["binding_price_cents"]) == (9, 600, 4092)
    terms, paths = mainnet_loan.saved(), collateral_path.saved()
    debt = row["draw_cents"] * 10000
    debt += ceil(Fraction(debt * terms["origination"], 2**60))
    debt = ceil(debt * collateral_path.accrual(2 * terms["apr"], 360))
    for cash in (0, 1067, 1068):
        ratios = []
        for trace in paths["traces"].values():
            anchor = dict(trace)[30]
            for step, ratio in trace:
                if step < 30:
                    continue
                price = floor(5500 * ratio / anchor)
                owing = ceil((debt - cash*10000) * collateral_path.accrual(2*terms["apr"], 12*(step-30)))
                ratios.append(ceil(owing * terms["usd_price"] * terms["factor"] * 10**7 / (row["pledge_lamports"] * price)))
        if cash == 0:
            assert max(ratios) == q["wait_peak_bps"] == 8194
        if cash == 1067:
            assert max(ratios) > 7000
        if cash == 1068:
            assert max(ratios) == q["paid_peak_bps"] == 7000


def test_insufficient_cash_liquidation_healthy_loan_and_no_extrapolated_midpoints():
    q = route(500, 5500)["cash_payoff"]["midtrip"]
    assert (q["payment_cents"], q["short_cents"], q["unused_cents"]) == (500,568,0)
    q = route(3000, 7500)["cash_payoff"]["midtrip"]
    assert (q["payment_cents"], q["unused_cents"], q["payday_cents"]) == (0,3000,7362)
    for price in (1,4000):
        q = route(3000, price)["cash_payoff"]["midtrip"]
        assert not q["eligible"] and "payment_cents" not in q
    path = {"traces": {"missing": [(0, 1), (60, 1)]}}
    with pytest.raises(ValueError, match="unavailable"):
        midtrip.checkpoints(path, 5500, mainnet_loan.saved()["apr"])
    for price in (None, True, 0, -1, 10000001):
        with pytest.raises(ValueError):
            route(3000, price)


@pytest.mark.parametrize("budget,minimum", [(0, 6438), (500, 6000), (3000, 4180)])
def test_minimum_day_15_price_respects_payment_capacity_and_pre_payment_liquidation(budget, minimum):
    row = dict(draw_cents=7298, pledge_lamports=2192000000)
    terms, paths = mainnet_loan.saved(), collateral_path.saved()
    for price in (minimum-1, minimum, minimum+1):
        marks, n = midtrip.checkpoints(paths, price, terms["apr"]*2)
        q = midtrip.quote(row, budget, marks, n, terms)
        assert (q["budget_price_cents"], q["no_payment_price_cents"]) == (minimum, 6438)
        assert (q["eligible"] and q["short_cents"] == 0) == (price >= minimum)


def test_early_cash_cannot_leave_a_subminimum_kamino_balance():
    path = {"traces": {"trip": [(30, Fraction(1)), (60, Fraction(7, 10))]}}
    marks, count = midtrip.checkpoints(path, 100, Decimal(0))
    terms = dict(origination=0, min_value=Decimal(".015"), usd_price=1, factor=100, liquidation=80)
    row = dict(draw_cents=2, pledge_lamports=30_000_000)
    partial = midtrip.quote(row, 1, marks, count, terms)
    assert (partial["needed_cents"], partial["payment_cents"], partial["short_cents"], partial["unused_cents"]) == (2, 0, 1, 1)
    assert partial["budget_price_cents"] == partial["no_payment_price_cents"]
    full = midtrip.quote(row, 2, marks, count, terms)
    assert full["payment_cents"] == 2 and full["payday_cents"] == 0


def test_replay_rejects_changed_remaining_debt_even_if_token_balances_match():
    q = partial_payment.saved()
    assert q["loan_raw"] - q["payment_raw"] == q["remaining_raw"] == 623000
    assert q["vendor_raw"] == 950000 and q["pledge_lamports"] == 21920000
    calls = json.loads(gzip.decompress(partial_payment.SAMPLE.read_bytes()))
    account = calls[-1]["response"]["value"]["accounts"][0]
    data = bytearray(base64.b64decode(account["data"][0]))
    data[1296] ^= 1
    account["data"][0] = base64.b64encode(data).decode()
    with pytest.raises(ValueError, match="did not reconcile"):
        atomic_fuel.replay_calls(calls, partial_payment.simulate)
