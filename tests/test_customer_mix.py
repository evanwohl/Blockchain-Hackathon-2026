from itertools import product

from freightos import customer_credit, mainnet_loan, road_cash
from freightos.fuel_floor import ceil_div


def test_full_size_plan_and_limited_or_exact_customer_terms():
    def mix(menu="partial", fee=100, amount=None, received=20000):
        q = road_cash.evaluate(8000, 7500, received, 300, 15000, offer_menu=menu, offer_fee=fee, offer_amount=amount)
        return q["mainnet"]["secured_loan"]["combined"]
    m = mix()
    r = m["plan"]
    assert (r["advance_cents"], r["draw_cents"], r["repay_cents"], r["pledge_lamports"]) == (5369, 3523, 3554, 1018500000)
    assert (r["invoice_balance_cents"], r["payday_cents"], m["without_loan"]["advance_cents"]) == (14576, 300, 8893)
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
        candidates = []
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
                if later >= 0 and opening <= budget and payday >= target:
                    candidates.append((advance, draw, payday))
        for key, eligible in (("plan", candidates), ("without_loan", [c for c in candidates if c[1] == 0])):
            row = m[key]
            got = (row["advance_cents"], row["draw_cents"], row["payday_cents"]) if row else None
            assert got == min(eligible, default=None)
