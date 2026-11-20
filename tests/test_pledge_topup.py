from fractions import Fraction
from math import ceil, floor

import pytest

from freightos import collateral_path, driver_desk, mainnet_loan, midtrip, partial_payment, pledge_topup


def route(limit, **changes):
    args = dict(bill=74500, invoice=310000, cash=25000, budget=60000, stable=15000, target=200000,
                stress=15000, offer_menu="counter", offer_amount=25000, offer_fee=500, borrower_state="new",
                stops=4, booked=2, balance=True, payoff_cash=3000, midtrip_price=5500,
                midtrip_drop_bps=3000, midtrip_topup_lamports=limit)
    args.update(changes)
    return args, driver_desk.quote(args)["series"]["first"]


@pytest.mark.parametrize("limit,cash,deposit,due", [(250_000_000,776,250_000_000,6583),(10**9,0,539_100_000,7362)])
def test_mixed_and_pledge_only_plans_bind_the_opening_cash_and_both_deposits(limit,cash,deposit,due):
    args, row = route(limit)
    q = row["cash_payoff"]["midtrip"]
    t = q["topup"]
    assert (t["payment_cents"], t["deposit_limit_lamports"], t["payday_cents"]) == (cash,deposit,due)
    assert t["peak_bps"] <= 7000 and t["cash_kept_cents"] == 1447-cash
    assert len(t["later_trials"]) == 4 and not any(v["missed"] for v in t["later_trials"])
    assert t["available_lamports"] >= limit and t["extra_shares"] < deposit
    selection = dict(plan=args, opening_lamports=row["opening_lamports"], payment_cents=cash, added_lamports=deposit//100)
    amounts = driver_desk.selected_topup(selection)
    result = partial_payment.saved(*amounts)
    assert result["loan_raw"] - result["remaining_raw"] == result["payment_raw"] == cash*100
    assert result["pledge_lamports"] == 21_920_000 + deposit//100 and result["added_lamports"] == deposit//100
    assert result["vendor_raw"] == 950000
    with pytest.raises(ValueError, match="differs"):
        driver_desk.selected_topup(dict(selection, added_lamports=deposit//100-1000))


def test_wallet_and_reserve_limits_and_no_action_after_liquidation():
    _, row = route(10**9, midtrip_price=4000)
    assert "topup" not in row["cash_payoff"]["midtrip"]
    _, row = route(10**9)
    terms = mainnet_loan.saved()
    marks, n = midtrip.checkpoints(collateral_path.saved(),5500,terms["apr"]*2)
    for room, wallet in ((42,10**9), (150_000,10**9), (10**9,20_000), (10**9,100_000)):
        t = midtrip.quote(row, 3000, marks, n, dict(terms, deposit_room=row["pledge_lamports"] + room),
                          drop_bps=3000, topup_limit=10**9, wallet_spare=wallet)["topup"]
        assert 0 <= t["deposit_limit_lamports"] <= min(room + 2,wallet)
        assert t["deposit_limit_lamports"] % pledge_topup.LOT == 0


def test_discrete_allocation_matches_exhaustive_token_mint_and_cash_choices():
    row = dict(draw_cents=28, pledge_lamports=8_000_000)
    terms = dict(mainnet_loan.saved(),collateral_supply_sf=43_000_000 << 60,collateral_shares=30_000_000)
    marks, n = midtrip.checkpoints(collateral_path.saved(),5500,terms["apr"]*2)
    base = Fraction(43,30)
    first = floor(row["pledge_lamports"]/base)
    backing, supply = 43_000_000+ceil(first*base), 30_000_000+first
    debt = 280000+ceil(Fraction(280000*terms["origination"],1<<60))
    debt = ceil(debt*marks[-1][3])
    constants = Fraction(terms["usd_price"])*terms["factor"]*10**7
    for cap in (0,1,4,11,29):
        t = midtrip.quote(row,100,marks,n,terms,drop_bps=3000,topup_limit=max(1,cap*100_000),wallet_spare=10**9)["topup"]
        choices=[]
        for k in range(cap+1):
            shares = k*100_000*supply//backing
            debit = ceil(Fraction(shares*backing,supply))
            represented = (first+shares)*(backing+debit)//(supply+shares)
            for cents in range(30):
                residual=max(0,debt-cents*10_000)
                if residual and cents and residual*terms["usd_price"]/10**6 < terms["min_value"]:
                    continue
                tests=[(p,g) for _,p,_,g,_ in marks]+[(3850,marks[-1][3])]
                if all(ceil(residual*g)*constants <= 7000*represented*p for p,g in tests):
                    choices.append((cents,k))
                    break
        assert (t["payment_cents"],t["deposit_limit_lamports"]//100_000)==min(choices)


def test_midtrip_quotes_reuse_opening_terms_without_sharing_driver_decisions():
    driver_desk._opening.cache_clear()
    args, row = route(250_000_000)
    row["cash_payoff"]["midtrip"]["topup"]["payment_cents"] = 0
    _, repeat = route(250_000_000)
    assert repeat["cash_payoff"]["midtrip"]["topup"]["payment_cents"] == 776
    _, changed = route(10**9)
    assert changed["cash_payoff"]["midtrip"]["topup"]["payment_cents"] == 0
    opening = {key: value for key, value in args.items() if not key.startswith("midtrip_") and key != "payoff_cash"}
    assert "cash_payoff" not in driver_desk.quote(opening)["series"]["first"]
    assert driver_desk._opening.cache_info().misses == 1
    driver_desk.quote(dict(opening, offer_fee=501))
    assert driver_desk._opening.cache_info().misses == 2
