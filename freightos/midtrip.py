"""Size a day-15 payment under the remaining halves of matched Gemini paths."""
from fractions import Fraction
from math import ceil, floor

from freightos import collateral_path, mainnet_loan

CENT = mainnet_loan.RAW_PER_CENT
MIDPOINT = 30


def checkpoints(path, price, annual_rate):
    worst = {}
    windows = 0
    for start, trace in path["traces"].items():
        anchor = dict(trace).get(MIDPOINT)
        if anchor is None:
            continue
        windows += 1
        for step, ratio in trace:
            if step < MIDPOINT:
                continue
            move = ratio / anchor
            if step not in worst or move < worst[step][0]:
                worst[step] = (move, start)
    if not windows or 60 not in worst:
        raise ValueError("Day-15 Gemini stress paths are unavailable")
    marks = []
    for step, (move, start) in sorted(worst.items()):
        exact_price = move * price
        cents = floor(exact_price) or exact_price
        growth = Fraction(collateral_path.accrual(annual_rate, (step-MIDPOINT)*12))
        marks.append((step, cents, start, growth, move))
    return marks, windows


def quote(row, budget, marks, windows, terms):
    pledge = row["pledge_lamports"]
    principal = row["draw_cents"] * CENT
    opening = principal + ceil(Fraction(principal * terms["origination"], 2**60))
    day15_debt = opening * marks[-1][3]
    debt = ceil(day15_debt)
    full_payoff = ceil(day15_debt / CENT)
    minimum_balance = Fraction(terms["min_value"]) * 10**6 / Fraction(terms["usd_price"])
    partial_limit = max(0, floor((day15_debt - minimum_balance) / CENT))
    spendable = full_payoff if budget >= full_payoff else min(budget, partial_limit)
    conversion = Fraction(terms["usd_price"]) * terms["factor"] * 10**7 / pledge
    threshold = terms["liquidation"] * 100
    target = threshold - 1000
    current_price = marks[0][1]
    current_ltv = debt * conversion / current_price

    def minimum_price(cash):
        remaining = max(0, debt - cash * CENT)
        opening_limit = floor(debt * conversion / threshold) + 1
        prices = [opening_limit]
        for _, _, _, growth, move in marks:
            required = ceil(remaining * growth) * conversion / target
            executable_mark = ceil(required) if required > 1 else required
            prices.append(ceil(executable_mark / move))
        return max(prices)

    base = dict(price_cents=current_price, current_ltv_bps=ceil(current_ltv), limit_bps=threshold,
                target_bps=target, windows=windows, eligible=current_ltv < threshold,
                no_payment_price_cents=minimum_price(0), budget_price_cents=minimum_price(spendable))
    if not base["eligible"]:
        return base

    # Invert the rounded debt limit at every checkpoint, then keep the binding limit.
    limit, binding = min((floor(floor(target * price / conversion) / growth), i)
                         for i, (_, price, _, growth, _) in enumerate(marks))
    needed = max(0, ceil(Fraction(debt - limit, CENT)))
    if needed > partial_limit:
        needed = full_payoff
    paid = min(needed, spendable)
    left = max(0, debt - paid * CENT)
    step, price, start, _, _ = marks[binding]

    def peak(raw):
        return max(ceil(ceil(raw * growth) * conversion / price) for _, price, _, growth, _ in marks)
    return {**base, "needed_cents": needed, "payment_cents": paid, "unused_cents": budget-paid,
            "short_cents": max(0, needed-budget), "payday_cents": ceil(Fraction(ceil(left * marks[-1][3]), CENT)),
            "wait_peak_bps": peak(debt), "paid_peak_bps": peak(left), "binding_hours": step*12,
            "binding_price_cents": floor(price), "binding_start": start}
