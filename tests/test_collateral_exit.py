import gzip
import json

import pytest

from freightos import atomic_fuel, collateral_exit, driver_desk, flash_exit, split_payment


def test_mainnet_payoff_matches_the_full_route_and_single_stop():
    inputs = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000, stress=15000,
                  offer_menu="counter", offer_amount=25000, offer_fee=500)
    series = driver_desk.quote(dict(inputs, borrower_state="new", stops=4, booked=2, balance=True))["series"]
    q = series["first"]["payoff_options"]
    assert [(o["cash_cents"], o["credit_cents"], o["sale_cents"], o["max_sol_lamports"], o["low_price_cash_floor_cents"])
            for o in q] == [(7362, 0, 0, 0, 7362), (3411, 3951, 3952, 547888540, 4644), (0, 7362, 7363, 1020775415, 0)]
    assert q[-1]["min_left_lamports"] == 1171224585
    single = driver_desk.quote(inputs)["mainnet"]["secured_loan"]["combined"]
    assert single["most_cash"]["payoff_options"][0]["cash_cents"] == single["most_cash"]["repay_cents"]
    assert "payoff_options" not in single["most_cash_without_loan"]


@pytest.mark.parametrize("fee", [0, 1, 2**60 // 3, 2**64 - 1])
def test_maximum_whole_cent_credit_matches_exhaustive_cash_and_collateral_constraints(fee):
    cost = [0, 8, 18, 28, 40, 52, 64, 80, 98]
    for pledge in range(11, 101, 3):
        for available in (10000, 19999, 55000, 1000000):
            row = dict(repay_cents=9, draw_cents=1, pledge_lamports=pledge)
            terms = dict(flash_fee_sf=fee, flash_available_raw=available, origination=1)
            for q in collateral_exit.options(row, cost, terms, 100):
                sales = []
                for d in range(10):
                    principal = d * 10000
                    if d and (fee == 2**64-1 or principal + 10001 > available):
                        continue
                    fee_cap = (principal * fee + 2**60 - 1) // 2**60
                    proceeds = (principal + fee_cap + 9999) // 10000
                    if proceeds < len(cost) and (pledge-cost[proceeds])*100 >= pledge*q["keep_pct"]:
                        sales.append(d)
                assert q["credit_cents"] == max(sales) and q["cash_cents"] + q["credit_cents"] == 9
                assert q["min_left_lamports"] + q["max_sol_lamports"] == pledge


def test_exit_replays_close_and_rejects_changed_legs_and_rollback():
    proof = flash_exit.saved()
    assert (proof["borrower_cash_raw"], proof["remaining_usdc_raw"], proof["repaid_raw"]) == (0, 0, split_payment.LOAN)
    assert proof["sold_lamports"] + proof["retained_lamports"] == split_payment.PLEDGE
    assert proof["sold_lamports"] <= proof["exit_cap_lamports"] <= split_payment.PLEDGE
    def calls():
        return json.loads(gzip.decompress(flash_exit.SAMPLE.read_bytes()))
    amended = calls()
    v = amended[-2]["response"]["value"]
    group = v["innerInstructions"][-1]
    group["instructions"][0]["parsed"]["info"]["tokenAmount"]["amount"] = "1"
    with pytest.raises(ValueError, match="temporary USDC"):
        atomic_fuel.replay_calls(amended, flash_exit.simulate)
    amended = calls()
    amended[-1]["response"]["value"]["postBalances"][0] += 1
    with pytest.raises(ValueError, match="roll back"):
        atomic_fuel.replay_calls(amended, flash_exit.simulate)
    amended = calls()
    amended[-2]["params"][0] = amended[-1]["params"][0]
    with pytest.raises(ValueError, match="recorded transaction"):
        atomic_fuel.replay_calls(amended, flash_exit.simulate)
