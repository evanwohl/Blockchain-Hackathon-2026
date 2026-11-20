from itertools import product

from freightos import customer_credit, mainnet_loan, road_cash
from freightos.fuel_floor import ceil_div


def test_full_size_plan_and_limited_or_exact_customer_terms():
    def mix(menu="partial", fee=100, amount=None, received=20000):
        q = road_cash.evaluate(8000, 7500, received, 300, 15000, offer_menu=menu, offer_fee=fee, offer_amount=amount)
        return q["mainnet"]["secured_loan"]["combined"]
    m = mix()
    r = m["plan"]
    assert (r["advance_cents"], r["draw_cents"], r["repay_cents"], r["pledge_lamports"]) == (5501, 3391, 3421, 1018500000)
    assert (r["invoice_balance_cents"], r["payday_cents"], m["without_loan"]["advance_cents"]) == (14443, 300, 8893)
    assert sum(r[k] for k in ("cash_cents", "stable_cents", "advance_cents", "draw_cents", "sale_cents")) == 19000
    assert r["advance_cents"] + r["fee_cents"] + r["invoice_balance_cents"] == 20000
    assert mix("both")["plan"]["advance_cents"] == 9900
    fixed = mix("counter", 300, 9000)
    assert fixed["without_loan"] is None
    assert (fixed["plan"]["advance_cents"], fixed["plan"]["draw_cents"]) == (9000, 91)
    assert mix("counter", 300, 9000, 9200)["plan"] is None
    assert mix("none")["plan"] is None


def test_prices_every_valid_split_against_an_independent_exhaustive_calendar():
    bill, stable, cash, network, rent = 22, 3, 4, 1400, 900000
    costs = [s * 135700 + s**2 for s in range(13)]
    schedule = [None if d in (1, 4) else (d * 278900, d + int(d > 0)) for d in range(bill - stable - cash + 1)]
    for budget, received, target, price, fee in product((0, 900000, 2300000, 6000000), (19, 29), (0, 3), (7000, 15000), (0, 1900)):
        policy = {"bill_cents": bill, "receipt_cents": received, "high_price_cents": price, "floor_cents": target}
        m = mainnet_loan.customer_mix(policy, costs, schedule, stable, cash, budget, network, rent, "partial", fee, None)
        candidates, funded, wallets = [], [], []
        for advance in range(received + 1):
            discount = customer_credit.customer_fee(advance, fee)
            own = min(cash, max(0, bill - advance))
            vendor = max(0, bill - own - advance)
            dollars = min(stable, vendor)
            for draw, term in enumerate(schedule):
                sale = vendor - dollars - draw
                if term is None or not 0 <= sale < len(costs):
                    continue
                pledge, repay = term
                used = costs[sale] + (network if vendor else 0) + (200000 if draw else 0)
                opening = used + pledge + (rent if sale else 0)
                later = received - advance - discount
                payday = later + max(0, advance - bill) - own - dollars - repay - ceil_div(used * price, 10**9)
                if later >= 0 and opening <= budget:
                    funded.append((advance, draw, payday))
                    if payday >= target:
                        candidates.append((advance, draw, payday))
                        wallets.append((opening, -payday, draw, advance))
        for key, group in (("keep_sol", wallets), ("keep_sol_without_loan", [k for k in wallets if k[2] == 0])):
            row = m[key]
            got = (row["opening_lamports"], -row["payday_cents"], row["draw_cents"], row["advance_cents"]) if row else None
            assert got == min(group, default=None)
        for key, eligible in (("plan", candidates), ("without_loan", [c for c in candidates if c[1] == 0])):
            row = m[key]
            got = (row["advance_cents"], row["draw_cents"], row["payday_cents"]) if row else None
            assert got == min(eligible, default=None)
        for key, eligible in (("most_cash", funded), ("most_cash_without_loan", [c for c in funded if c[1] == 0])):
            row = m[key]
            got = (row["advance_cents"], row["draw_cents"], row["payday_cents"]) if row else None
            assert got == min(eligible, key=lambda c: (-c[2], c[1], c[0]), default=None)


def test_maximum_payday_cash_compares_identical_customer_terms_and_keeps_below_target_balances():
    def mix(fee, floor):
        return road_cash.evaluate(8000, 7500, 20000, floor, 15000, offer_menu="counter",
                                  offer_fee=fee, offer_amount=9000)["mainnet"]["secured_loan"]["combined"]
    q = mix(300, 300)
    best, free = q["most_cash"], q["most_cash_without_loan"]
    assert best["advance_cents"] == free["advance_cents"] == 9000
    assert best["fee_cents"] == free["fee_cents"] == 300
    assert (best["draw_cents"], best["sale_cents"], best["repay_cents"], best["payday_cents"]) == (457, 0, 461, 692)
    assert (free["draw_cents"], free["sale_cents"], free["payday_cents"]) == (0, 457, 206)
    assert best["path_check"]["peak_ltv_bps"] <= 7000
    assert q["prices"] == [dict(price_cents=p, allowed_cents=a, debt_free_cents=b)
                           for p, a, b in ((4984, 694, 841), (7282, 694, 695), (15000, 692, 206))]
    assert q["history"] == {"windows": 9, "ahead": 6, "ties": 0}
    high_fee = mix(1000, 300)
    assert high_fee["plan"] is None and high_fee["most_cash"]["payday_cents"] == -8
    assert high_fee["most_cash_without_loan"]["payday_cents"] == -494


def test_certified_boundaries_match_all_draws_at_rent_discontinuity_and_payday_ties(monkeypatch):
    bill, base, network, rent = 320, 10**7, 5000, 9_000_000
    costs = [s * base for s in range(bill + 1)]
    schedule = [(0, 0), None] + [(d * (base + 1000), d + d // 23) for d in range(2, bill + 1)]
    def mix(cap, floor):
        policy = dict(bill_cents=bill, receipt_cents=820, high_price_cents=200, floor_cents=floor)
        return mainnet_loan.customer_mix(policy, costs, schedule, 0, 0, cap, network, rent, "partial", 1000, None)
    cases = list(product((base * 20, base * 300, base * 320 + 600000, base * 340), (0, 473, 482, 494, 499)))
    original = mainnet_loan._boundary_draws
    used = []
    def observe(*args):
        result = original(*args)
        used.append(len(result))
        return result
    monkeypatch.setattr(mainnet_loan, "_boundary_draws", observe)
    actual = [mix(*case) for case in cases]
    assert used and max(used) <= 3
    monkeypatch.setattr(mainnet_loan, "_ordered_draws", lambda *args: None)
    assert actual == [mix(*case) for case in cases]
