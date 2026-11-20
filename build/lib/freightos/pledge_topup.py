"""Compare a spare-SOL pledge with a cash-only payment on the same loan."""
from bisect import bisect_left
from fractions import Fraction
from math import ceil, floor

LOT = 100_000


def deposited(amount, backing, supply):
    shares = floor(amount * supply / backing)
    return shares, ceil(shares * backing / supply)


def allocate(original, cap, wallet_spare, terms, minimum_opening, spendable, required_cash):
    backing = Fraction(terms["collateral_supply_sf"], 1 << 60)
    supply = terms["collateral_shares"]
    first, debit = deposited(original, backing, supply)
    backing += debit
    supply += first
    room = max(0, terms["deposit_room"] - debit)
    lots = min(cap, wallet_spare, room) // LOT

    def position(units):
        shares, used = deposited(units * LOT, backing, supply)
        represented = floor((first + shares) * (backing + used) / (supply + shares))
        return represented, shares, used

    initial = position(0)[0]
    result = dict(cap_lamports=cap, available_lamports=wallet_spare, lot_lamports=LOT, max_lots=lots)
    if initial < minimum_opening:
        return {**result, "eligible": False}

    smallest_cash = required_cash(position(lots)[0])
    paid = min(smallest_cash, spendable)

    def sufficient(units):
        return required_cash(position(units)[0]) <= paid

    units = bisect_left(range(lots + 1), True, key=sufficient) if smallest_cash <= spendable else lots
    represented, shares, used = position(units)
    return {**result, "eligible": True, "payment_cents": paid, "needed_cents": smallest_cash,
            "deposit_limit_lamports": units * LOT, "deposit_lamports": used, "extra_shares": shares,
            "represented_lamports": represented, "initial_represented_lamports": initial}
