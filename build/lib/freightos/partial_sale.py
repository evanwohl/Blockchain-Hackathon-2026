"""Size a capped Orca sale and Kamino top-up with the driver's road cash fixed."""
from bisect import bisect_left
from fractions import Fraction
from functools import cache
from math import ceil, floor

from freightos import mainnet_loan, midtrip, payoff_budget, pledge_topup, pledge_yield, stop_backup

SOL = mainnet_loan.LAMPORTS


def choose(row, decision, path, terms, marks, pools, loss):
    topup = decision["topup"]
    sale = topup["sale"]
    price = decision["price_cents"]
    growth = marks[-1][3]
    health = [(p, g) for _, p, _, g, _ in marks]
    cases = []
    rates = pledge_yield.saved()
    initial, reference = pledge_yield.positions(topup, row["pledge_lamports"], terms)
    for start, trace in sorted(path["traces"].items()):
        points = dict(trace)
        if 30 in points and 60 in points:
            hourly_growth = pledge_yield.share_growth(start, rates)[1]
            cases.append((price * points[60] / points[30], hourly_growth))
    drop = decision.get("downside")
    if drop:
        stress = Fraction(price * (10_000 - drop["drop_bps"]), 10_000)
        cases.append((stress, cases[-1][1]))
        health.append((floor(stress) or stress, growth))

    ref_used = topup["deposit_lamports"]
    cap = topup["deposit_limit_lamports"]
    upper = sale["output_cents"] - 1
    for mark, gain in cases:
        slope = mark / price - growth
        if slope > 0:
            # A deposit cannot add more redeemable SOL than it debits. This bounds earnings even with share rounding.
            missed = floor(reference * gain) - ref_used - floor(initial * gain + max(0, ref_used * (gain-1)))
            upper = min(upper, floor((loss + 1 - missed * mark / SOL) / slope))

    supply = terms["collateral_shares"]
    backing = Fraction(terms["collateral_supply_sf"], 1 << 60)
    first, debit = pledge_topup.deposited(row["pledge_lamports"], backing, supply)
    backing += debit
    supply += first
    lots = cap // pledge_topup.LOT

    @cache
    def position(n):
        shares, used = pledge_topup.deposited(n * pledge_topup.LOT, backing, supply)
        return (first + shares) * (backing + used) / (supply + shares), used

    factor = Fraction(terms["usd_price"]) * terms["factor"] * 10**7 / decision["target_bps"]
    for proceeds in range(upper, 0, -1):
        paid = topup["payment_cents"] + proceeds
        if paid > decision["partial_limit_cents"]:
            continue
        residual = decision["day15_debt_raw"] - paid * midtrip.CENT
        needed = max(ceil(ceil(residual*g) * factor / p) for p, g in health)
        n = bisect_left(range(lots + 1), needed, key=lambda k: floor(position(k)[0]))
        deposit = n * pledge_topup.LOT
        if n > lots or not deposit:
            continue
        after, used = position(n)
        due = ceil(Fraction(ceil(residual*growth), midtrip.CENT))
        reduction = topup["payday_cents"] - due
        limits = []
        values = []
        for mark, gain in cases:
            missed = floor(reference*gain) - floor(after*gain) - ref_used + used
            values.append((mark, missed))
            limits.append(floor((reduction+loss)*SOL/mark) - missed)
        spot = ceil(Fraction(proceeds*SOL, price))
        maximum = min(cap-deposit, stop_backup.buffered(spot), *limits)
        if maximum < spot:
            continue
        spend = payoff_budget.modeled_input(pools, proceeds, price)
        if spend is not None and spend <= maximum:
            worst = min(floor(reduction - (maximum+missed)*mark/SOL) for mark, missed in values)
            return dict(worst_difference_cents=worst, output_cents=proceeds, cash_cents=topup["payment_cents"], payment_cents=paid, payday_cents=due,
                        max_lamports=maximum, modeled_input_lamports=spend, deposit_limit_lamports=deposit,
                        deposit_lamports=used, extra_shares=pledge_topup.deposited(deposit, backing, supply)[0], represented_lamports=floor(after), unused_cents=topup["unused_cents"],
                        freed_lamports=cap-deposit-maximum, loss_limit_cents=loss)
