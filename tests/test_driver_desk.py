import pytest

from freightos import driver_desk, fuel_floor, mainnet_stop, stop_backup
from freightos.fuel_floor import ceil_div


CASE = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=300,
            stress=15000, offer_menu="counter", offer_fee=500, offer_amount=25000)


def test_real_bill_conserves_funds_and_prices_both_sides_of_the_crossover():
    quote = driver_desk.quote(CASE)
    mix = quote["mainnet"]["secured_loan"]["combined"]
    loan, sale = mix["most_cash"], mix["most_cash_without_loan"]
    assert quote["reference_cents"] == 7247
    assert (loan["draw_cents"], loan["repay_cents"], loan["pledge_lamports"]) == (9500, 9583, 2853400000)
    for row in (loan, sale):
        assert row["invoice_balance_cents"] == 284500
        assert sum(row[k] for k in ("advance_cents", "cash_cents", "stable_cents", "draw_cents", "sale_cents")) == CASE["bill"]
    assert sale["sale_cents"] == 9500
    assert [(p["allowed_cents"] - p["debt_free_cents"]) for p in mix["prices"]] == [-3052, -40, 10169]
    switch = mix["switch_prices"]
    assert switch == dict(borrowing_from=7279, debt_free_through=7276)
    def balance(row, price):
        raw = row["restore_lamports"]
        return row["payday_cents"] + ceil_div(raw * 15000, 10**9) - ceil_div(raw * price, 10**9)
    for price in range(20001):
        difference = balance(loan, price) - balance(sale, price)
        if price >= switch["borrowing_from"]:
            assert difference > 0
        elif price <= switch["debt_free_through"]:
            assert difference < 0


def test_customer_can_cover_more_than_the_bill_and_cash_can_cover_it_without_an_invoice():
    mix = driver_desk.quote(dict(CASE, bill=20101, invoice=25500, budget=0, cash=0, stable=0))["mainnet"]["secured_loan"]["combined"]
    row = mix["most_cash"]
    assert row["surplus_cents"] == 4899 and row["invoice_balance_cents"] == 0
    assert row["advance_cents"] - row["surplus_cents"] == 20101
    cash = dict(CASE, bill=250000, cash=250000, invoice=0, stable=0, budget=0, offer_menu="none", offer_fee=0, offer_amount=None)
    mix = driver_desk.quote(cash)["mainnet"]["secured_loan"]["combined"]
    assert mix["most_cash"]["cash_cents"] == 250000 and mix["most_cash"]["payday_cents"] == -250000
    assert mix["plan"] is None
    assert driver_desk.quote(dict(cash, cash=0))["mainnet"]["secured_loan"]["combined"]["most_cash"] is None


@pytest.mark.parametrize("changed", [dict(bill=0), dict(bill=250001), dict(invoice=25499), dict(invoice=True), dict(stable=-1), dict(stress=0)])
def test_desk_rejects_out_of_range_terms(changed):
    with pytest.raises(ValueError):
        driver_desk.quote(dict(CASE, **changed))


@pytest.mark.parametrize("capacity", [(12, 17), (17, 12), (0, 17), (17, 0)])
def test_separated_pool_marginals_and_capacity_match_exhaustive_routing(capacity):
    pools = [dict(price=fuel_floor.Q64 * scale, liquidity=10**13, fee=300, max_raw=c * 10000)
             for scale, c in zip((1, 2), capacity)]
    for order in (pools, pools[::-1]):
        costs, legs = mainnet_stop.split_costs(order, 24)
        for total, (cost, second) in enumerate(zip(costs, legs)):
            choices = [(sum(stop_backup.buffered(fuel_floor.swap_cost(p, n * 10000))
                            for p, n in zip(order, (total-s, s))), s)
                       for s in range(max(0, total-order[0]["max_raw"]//10000),
                                      min(total, order[1]["max_raw"]//10000)+1)]
            assert (cost, second) == min(choices)


def test_unverified_snapshot_cannot_price_the_bill(monkeypatch):
    def unavailable():
        raise ValueError("Observation hash mismatch")
    monkeypatch.setattr(driver_desk.mainnet_loan, "saved", unavailable)
    with pytest.raises(RuntimeError, match="could not be verified"):
        driver_desk.quote(CASE)


def test_wallet_limit_uses_the_unrounded_collateral_mark():
    case = dict(CASE, bill=250, invoice=1000, cash=0, stable=0, target=0,
                offer_menu="none", offer_amount=None, offer_fee=0)
    def debt_free(budget):
        return driver_desk.quote(dict(case, budget=budget))["mainnet"]["secured_loan"]["combined"]["most_cash_without_loan"]
    assert debt_free(262) is None
    assert debt_free(263)["sale_cents"] == 250


def test_keep_more_sol_splits_the_sale_and_loan_while_meeting_the_payday_floor():
    mix = driver_desk.quote(dict(CASE, target=230000))["mainnet"]["secured_loan"]["combined"]
    row = mix["keep_sol"]
    assert (row["sale_cents"], row["draw_cents"], row["repay_cents"]) == (4588, 4912, 4955)
    assert (row["available_sol_value_cents"], row["payday_cents"]) == (44685, 230001)
    assert row["pledge_lamports"] == 1475400000 and row["path_check"]["peak_ltv_bps"] <= 7000
    best = mix["most_cash"]
    assert best["available_sol_value_cents"] == 39319
    assert row["available_lamports"] - best["available_lamports"] == best["opening_lamports"] - row["opening_lamports"]
    assert mix["keep_sol_without_loan"] is None and mix["most_cash_without_loan"]["payday_cents"] == 224744


@pytest.mark.parametrize(("floor", "expected"), [(224744, (0, 9500)), (224745, (5, 9495)), (234913, (9500, 0)), (234914, None)])
def test_wallet_frontier_changes_at_exact_payday_floors(floor, expected):
    row = driver_desk.quote(dict(CASE, target=floor))["mainnet"]["secured_loan"]["combined"]["keep_sol"]
    assert ((row["draw_cents"], row["sale_cents"]) if row else None) == expected
