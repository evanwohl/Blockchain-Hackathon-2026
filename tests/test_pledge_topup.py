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


@pytest.mark.parametrize("limit,output,cap,due,crossover", [
    (250_000_000, 671, 122_610_000, 5909, 5498),
    (10**9, 1447, 264_406_365, 5909, 5496),
])
def test_sale_preserves_road_cash_and_replays_borrower_swap_with_a_binding_limit(limit, output, cap, due, crossover):
    args, row = route(limit)
    topup = row["cash_payoff"]["midtrip"]["topup"]
    sale = topup["sale"]
    assert sale["payment_cents"] == topup["payment_cents"] + output
    assert sale["payday_cents"] == due and sale["max_lamports"] == cap <= topup["deposit_limit_lamports"]
    outcomes = sale["outcomes"]
    assert outcomes["stress_difference_cents"] == (topup["payday_cents"] - due) - ceil(Fraction(cap * 3850, 10**9))
    assert len(outcomes["histories"]) == 9 and sum(h["difference_cents"] > 0 for h in outcomes["histories"]) == 3
    assert sale["crossover_cents"] == crossover
    for dollars, cents in ((sale["min_net_price_cents"], output), (crossover, topup["payday_cents"] - due)):
        assert (dollars - 1) * cap < cents * 10**9 <= dollars * cap
    request = dict(plan=args, opening_lamports=row["opening_lamports"], **sale["preview"])
    proof = partial_payment.saved(*driver_desk.selected_topup(request))
    assert proof["loan_raw"] - proof["remaining_raw"] == proof["cash_raw"] + proof["sale_raw"] == 144_700
    assert proof["cash_raw"] == sale["cash_cents"] * 100 and proof["sale_raw"] == output * 100
    assert 0 < proof["sold_lamports"] <= proof["sale_cap"] == cap // 100
    assert proof["rollback_slot"] >= proof["slot"] and proof["vendor_raw"] == 950_000
    assert proof["pledge_lamports"] * 100 == row["pledge_lamports"]
    with pytest.raises(ValueError, match="differs"):
        driver_desk.selected_topup(dict(request, sale_cap=cap//100+1))
    with pytest.raises(ValueError, match="differs"):
        driver_desk.selected_topup(dict(request, output_cents=output-1))


def test_sale_needs_an_eligible_pledge_that_meets_the_tested_cushion():
    for args in (dict(payoff_cash=500), dict(midtrip_price=4000), dict(midtrip_price=15000)):
        _, row = route(250_000_000, **args)
        assert "sale" not in row["cash_payoff"]["midtrip"].get("topup", {})


@pytest.mark.parametrize("limit,strict_cap,price,stress,extra", [
    (250_000_000, 84_598_004, 7932, 346, 411_940),
    (10**9, 182_521_121, 7928, 746, 888_308),
])
def test_sale_cap_accounts_for_collateral_earnings_and_rejects_one_more_lamport(limit, strict_cap, price, stress, extra):
    args, row = route(limit, sale_policy="historical")
    topup = row["cash_payoff"]["midtrip"]["topup"]
    sale = topup["sale"]
    carry = sale["outcomes"]["carry"]
    assert sale["max_lamports"] == strict_cap < sale["regular_cap_lamports"]
    assert sale["min_net_price_cents"] == price
    assert carry["extra_lamports"] == extra and carry["stress_difference_cents"] == stress
    assert len(carry["histories"]) == 9
    reduction = topup["payday_cents"] - sale["payday_cents"]
    with_one_more = []
    for history in carry["histories"]:
        trace = dict(collateral_path.saved()["traces"][history["start"]])
        mark = 5500 * trace[60] / trace[30]
        sold_and_earnings = strict_cap + history["extra_lamports"]
        assert history["difference_cents"] == floor(reduction - sold_and_earnings * mark / 10**9) >= 1
        with_one_more.append(floor(reduction - (sold_and_earnings+1) * mark / 10**9))
    assert min(with_one_more) == 0
    request = dict(plan=args, opening_lamports=row["opening_lamports"], **sale["preview"])
    proof = partial_payment.saved(*driver_desk.selected_topup(request))
    assert 0 < proof["sold_lamports"] <= proof["sale_cap"] == strict_cap // 100
    assert proof["vendor_raw"] == 950_000 and proof["remaining_raw"] == 585_100 and proof["rollback_slot"] >= proof["slot"]
    with pytest.raises(ValueError, match="differs"):
        driver_desk.selected_topup(dict(request, plan=dict(args, sale_policy="standard")))


def test_missing_yield_evidence_preserves_basic_quote_and_withholds_strict_sale(monkeypatch):
    def missing():
        raise ValueError("Evidence unavailable")

    monkeypatch.setattr(driver_desk.pledge_yield, "saved", missing)
    _, row = route(250_000_000)
    sale = row["cash_payoff"]["midtrip"]["topup"]["sale"]
    assert sale["outcomes"]["carry"] is None and sale["preview"]["sale_cap"] == 1_226_100
    _, row = route(250_000_000, sale_policy="historical")
    sale = row["cash_payoff"]["midtrip"]["topup"]["sale"]
    assert sale["max_lamports"] == 0 and "preview" not in sale


@pytest.mark.parametrize("limit,minimum,spend,cap", [
    (250_000_000, 297, 122_233_227, 122_302_932),
    (10**9, 640, None, 263_624_675),
])
def test_minimum_downside_to_fill_and_public_sale_replay(limit, minimum, spend, cap):
    args, row = route(limit, sale_policy="budget", sale_loss_cents=minimum)
    s = row["cash_payoff"]["midtrip"]["topup"]["sale"]
    carry = s["outcomes"]["carry"]
    assert carry["minimum_loss_limit_cents"] == minimum and s["modeled_fill"]
    if spend is not None:
        assert s["modeled_input_lamports"] == spend
    assert s["modeled_input_lamports"] <= s["max_lamports"] == cap
    assert min(h["difference_cents"] for h in carry["histories"]) == -minimum
    _, previous = route(limit, sale_policy="budget", sale_loss_cents=minimum-1)
    previous_sale = previous["cash_payoff"]["midtrip"]["topup"]["sale"]
    assert previous_sale["modeled_input_lamports"] == s["modeled_input_lamports"] > previous_sale["max_lamports"]
    assert not previous_sale["modeled_fill"]
    assert [t["action"] for t in previous_sale["later_trips"]] == ["pledge"]*4
    assert [t["difference_cents"] for t in previous_sale["later_trips"]] == [0]*4
    request = dict(plan=args, opening_lamports=row["opening_lamports"], **s["preview"])
    proof = partial_payment.saved(*driver_desk.selected_topup(request))
    assert proof["sold_lamports"] <= proof["sale_cap"] == cap//100
    assert proof["sale_raw"] == s["output_cents"] * 100 and proof["remaining_raw"] == 585_100
    with pytest.raises(ValueError, match="differs"):
        driver_desk.selected_topup(dict(request, plan=dict(args,sale_loss_cents=minimum-1)))


def test_later_payday_changes_the_score_but_cannot_size_its_own_sale(monkeypatch):
    _, row = route(250_000_000, sale_policy="budget", sale_loss_cents=297)
    base = row["cash_payoff"]["midtrip"]["topup"]["sale"]["later_trips"]
    assert [t["difference_cents"] for t in base] == [-135,112,138,154]
    assert [t["prior_windows"] for t in base] == [1,6,6,6]
    original = collateral_path.saved()
    start = base[-1]["start"]
    altered = [(step, ratio * (2 if step == 60 else 1)) for step,ratio in original["traces"][start]]
    monkeypatch.setattr(collateral_path,"saved",lambda: dict(original,traces={**original["traces"],start:altered}))
    _, row = route(250_000_000, sale_policy="budget", sale_loss_cents=297)
    later = row["cash_payoff"]["midtrip"]["topup"]["sale"]["later_trips"]
    assert later[:-1] == base[:-1]
    assert {k:v for k,v in later[-1].items() if k != "difference_cents"} == {k:v for k,v in base[-1].items() if k != "difference_cents"}
    assert later[-1]["difference_cents"] < -297


def test_unavailable_pool_depth_has_no_fill_and_no_spend_threshold(monkeypatch):
    monkeypatch.setattr(driver_desk.sale_replay, "modeled_input", lambda *args: None)
    _, row = route(250_000_000, sale_policy="budget", sale_loss_cents=297)
    s = row["cash_payoff"]["midtrip"]["topup"]["sale"]
    assert s["modeled_input_lamports"] is None and not s["modeled_fill"]
    assert "minimum_loss_limit_cents" not in s["outcomes"]["carry"]
    assert all(t["action"] == "pledge" for t in s["later_trips"])


@pytest.mark.parametrize("limit,deposit,owed,road", [(250_000_000,165_800_000,6356,2224), (10**9,454_900_000,7135,3000)])
def test_smaller_swap_and_pledge_share_a_single_limit_and_public_obligation(limit,deposit,owed,road):
    args, row = route(limit, sale_policy="budget", sale_loss_cents=100)
    topup = row["cash_payoff"]["midtrip"]["topup"]
    sale = topup["sale"]
    split = sale["split"]
    assert not sale["modeled_fill"] and split["output_cents"] == 226
    assert (split["deposit_limit_lamports"], split["payday_cents"], split["unused_cents"]) == (deposit, owed, road)
    assert split["modeled_input_lamports"] <= split["max_lamports"] == 41_187_477
    assert deposit + split["max_lamports"] + split["freed_lamports"] == topup["deposit_limit_lamports"]
    assert split["worst_difference_cents"] >= -100
    selection = dict(plan=args,opening_lamports=row["opening_lamports"],**split["preview"])
    proof = partial_payment.saved(*driver_desk.selected_topup(selection))
    assert proof["added_lamports"] == deposit//100 and proof["sale_raw"] == 22_600
    assert proof["vendor_raw"] == 950_000 and proof["pledge_lamports"] == 21_920_000 + deposit//100
    assert proof["sale_raw"] + proof["cash_raw"] == proof["loan_raw"] - proof["remaining_raw"] == split["payment_cents"]*100
    assert proof["rollback_slot"] >= proof["slot"]
    for key in ("payment_cents","added_lamports","sale_cap","output_cents"):
        with pytest.raises(ValueError, match="differs"):
            driver_desk.selected_topup(dict(selection, **{key:selection[key]-1}))


def test_split_search_matches_exhaustive_cent_search_and_minimum_healthy_deposit():
    from bisect import bisect_left
    from freightos import payoff_budget, pledge_yield
    _, row = route(250_000_000,sale_policy="budget",sale_loss_cents=100)
    decision = row["cash_payoff"]["midtrip"]
    t = decision["topup"]
    s = t["sale"]["split"]
    terms = mainnet_loan.saved()
    path = collateral_path.saved()
    marks, _ = midtrip.checkpoints(path,5500,terms["apr"]*2)
    tests = [(price, growth) for _, price, _, growth, _ in marks] + [(3850,marks[-1][3])]
    factor = Fraction(terms["usd_price"])*terms["factor"]*10**7 / decision["target_bps"]
    backing = Fraction(terms["collateral_supply_sf"],1<<60)
    supply = terms["collateral_shares"]
    first, used = pledge_topup.deposited(row["pledge_lamports"],backing,supply)
    backing += used
    supply += first
    claims = []
    debits = []
    for deposit in range(0,t["deposit_limit_lamports"]+1,100_000):
        shares, debit = pledge_topup.deposited(deposit,backing,supply)
        claims.append((first+shares)*(backing+debit)/(supply+shares))
        debits.append(debit)
    rates = pledge_yield.saved()
    finishes = []
    for start, trace in sorted(path["traces"].items()):
        pts = dict(trace)
        finishes.append((5500*pts[60]/pts[30],pledge_yield.share_growth(start,rates)[1]))
    finishes.append((3850,finishes[-1][1]))
    pools = driver_desk.mainnet_stop.saved()[1]
    for proceeds in range(t["sale"]["output_cents"], 0, -1):
        debt = decision["day15_debt_raw"] - (proceeds+t["payment_cents"])*10_000
        payday = ceil(debt*marks[-1][3]/10_000)
        required = max(ceil(ceil(debt*g)*factor/p) for p,g in tests)
        n = bisect_left(claims,required)
        spend = payoff_budget.modeled_input(pools,proceeds,5500)
        if n == len(claims) or spend+100_000*n > t["deposit_limit_lamports"]:
            continue
        def advantage(mark, growth):
            missed = floor(claims[-1]*growth)-floor(claims[n]*growth)-debits[-1]+debits[n]
            return floor(t["payday_cents"]-payday-(spend+missed)*Fraction(mark)/10**9)
        if min(advantage(*case) for case in finishes) >= -100:
            assert (proceeds,n*100_000) == (s["output_cents"],s["deposit_limit_lamports"])
            break
    else:
        pytest.fail("No feasible split")
    assert [r["split_option"]["difference_cents"] for r in t["sale"]["later_trips"]] == [-46,37,46,51]
