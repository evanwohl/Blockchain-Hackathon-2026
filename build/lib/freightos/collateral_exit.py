"""Quote how much of a fuel-loan payoff can come from released collateral."""
from bisect import bisect_right

from freightos.fuel_floor import ceil_div
from freightos.mainnet_loan import LAMPORTS, RAW_PER_CENT

FEE_SCALE = 1 << 60
FLASH_DISABLED = (1 << 64) - 1


def sale_cents(draw, fee_sf):
    raw = draw * RAW_PER_CENT
    return ceil_div(raw + ceil_div(raw * fee_sf, FEE_SCALE), RAW_PER_CENT)


def capacity_cents(row, lending):
    principal = row["draw_cents"] * RAW_PER_CENT
    origination = ceil_div(principal * lending["origination"], FEE_SCALE)
    available = max(0, lending["flash_available_raw"] - principal - origination)
    return min(row["repay_cents"], available // RAW_PER_CENT) if lending["flash_fee_sf"] != FLASH_DISABLED else 0


def options(row, costs, lending, low_price):
    debt, pledge = row["repay_cents"], row["pledge_lamports"]
    fee = lending["flash_fee_sf"]
    capacity = capacity_cents(row, lending)
    choices = []
    for keep in (100, 75, 0):
        spendable = pledge - ceil_div(pledge * keep, 100)
        sale_limit = bisect_right(costs, spendable) - 1
        lo, hi = 0, capacity
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sale_cents(mid, fee) <= sale_limit:
                lo = mid
            else:
                hi = mid - 1
        sale = sale_cents(lo, fee)
        used = costs[sale]
        choices.append(dict(keep_pct=keep, cash_cents=debt-lo, credit_cents=lo, sale_cents=sale,
                            max_sol_lamports=used, min_left_lamports=pledge-used,
                            fee_cap_raw=ceil_div(lo * RAW_PER_CENT * fee, FEE_SCALE),
                            low_price_cash_floor_cents=max(0, debt-min(capacity, spendable*low_price//LAMPORTS))))
    return choices
