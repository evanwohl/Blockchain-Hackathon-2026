"""Size an exact-output collateral sale for a driver's cash contribution."""
from bisect import bisect_left, bisect_right
from fractions import Fraction
from functools import cache
from math import ceil

from freightos import collateral_exit, fuel_floor, mainnet_loan, stop_backup

Q = fuel_floor.Q64
CENT = mainnet_loan.RAW_PER_CENT
SOL = mainnet_loan.LAMPORTS


def raw_sale(pools, output):
    left, right = pools
    low, high = max(0, output - left["max_raw"]), min(output, right["max_raw"])
    if low > high:
        return None

    def ideal(pool, amount):
        price, liquidity = pool["price"], pool["liquidity"]
        return Fraction(amount * Q**2 * liquidity * stop_backup.SWAP_CAP_BPS * 100,
                        price * (liquidity * price - amount * Q) * (1_000_000 - pool["fee"]))

    def lower_bound(k):
        return ideal(left, output-k) + ideal(right, k)

    def buffered_cost(k):
        return sum(stop_backup.buffered(fuel_floor.swap_cost(pool, amt))
                   for pool, amt in zip(pools, (output-k, k)))

    # The unrounded pool costs are convex lower bounds on every executable integer split.
    center = bisect_left(range(low, high), True, key=lambda k: lower_bound(k+1) >= lower_bound(k)) + low
    best = min((buffered_cost(k), k) for k in {low, center, high})
    if lower_bound(center) <= best[0] - 1:
        start = low + bisect_left(range(low, center), True, key=lambda k: lower_bound(k) <= best[0]-1)
        stop = center + bisect_right(range(center, high+1), False, key=lambda k: lower_bound(k) > best[0]-1)
        for k in range(start, stop):
            best = min(best, (buffered_cost(k), k))
    cost, second = best
    return dict(max_lamports=cost, legs=[dict(pool=p["pool"], output_raw=n) for p, n in zip(pools, (output-second, second)) if n])


def modeled_input(pools, proceeds, price):
    route = raw_sale(pools, proceeds * CENT)
    if route is None:
        return None
    by_address = {pool["pool"]: pool for pool in pools}
    spent = sum(fuel_floor.swap_cost(by_address[leg["pool"]], leg["output_raw"]) for leg in route["legs"])
    spot = Fraction(max(pool["price"]**2 for pool in pools) * SOL, fuel_floor.Q64**2 * CENT)
    return ceil(spent * spot / price)


def quote(row, budget, pools, lending, prices):
    debt, pledge, fee = row["repay_cents"], row["pledge_lamports"], lending["flash_fee_sf"]
    capacity = collateral_exit.capacity_cents(row, lending)
    wanted = max(0, debt-budget)

    @cache
    def sell(draw):
        raw = draw * CENT
        fee_raw = fuel_floor.ceil_div(raw * fee, collateral_exit.FEE_SCALE)
        sale = raw_sale(pools, raw+fee_raw)
        return {**sale, "output_raw": raw+fee_raw, "fee_cap_raw": fee_raw} if sale is not None else None

    def affordable(requested, allowance):
        limit = min(requested, capacity)
        def exceeds(d):
            sale = sell(d)
            return sale is None or sale["max_lamports"] > allowance
        if not exceeds(limit):
            return limit
        return bisect_left(range(limit+1), True, key=exceeds)-1

    draw = affordable(wanted, pledge)
    sale = sell(draw)
    cash = debt-draw
    quarter = pledge - fuel_floor.ceil_div(pledge*75, 100)
    three_quarter_cash = debt-affordable(debt, quarter)
    target_raw = wanted * CENT + fuel_floor.ceil_div(wanted*CENT*fee, collateral_exit.FEE_SCALE)
    required_price = fuel_floor.ceil_div(target_raw*SOL, quarter*CENT) if quarter and wanted <= capacity else None
    distinct = sorted(set(prices))
    return {**sale, "budget_cents": budget, "credit_cents": draw, "cash_required_cents": cash,
            "short_cents": max(0, cash-budget), "unused_cents": max(0, budget-cash),
            "min_left_lamports": pledge-sale["max_lamports"], "price_for_three_quarters_cents": required_price,
            "prices_below": sum(p < required_price for p in distinct) if required_price is not None else None,
            "price_count": len(distinct), "three_quarter_cash_cents": three_quarter_cash}
