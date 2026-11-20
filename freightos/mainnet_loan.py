"""Compare a protected SOL sale with a USDC draw against SOL for the same fuel bill."""
import hashlib
import json
from decimal import Decimal
from functools import cache
from pathlib import Path

import numpy as np
from solders.pubkey import Pubkey

from freightos import customer_credit, freight_credit as credit, reserve, reserve_credit
from freightos.fuel_floor import ceil_div

EVIDENCE = Path(__file__).with_name("artifacts") / "mainnet_fuel" / "lending.json"
DIGEST = "604862b55bc98ecb8d90da86b713fa027f6992da4364493b26240e84d186aa89"
MARKET = "7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5PfF"
SOL_RESERVE = "d4A2prbA2whesmvHaL88BH6Ewn5N4bTSU2Ze8P6Bc4Q"
USDC_RESERVE = "D6q6wuQSrifJKZYpR1M8R4YawnLDtDsMmWM1NbBmgJ59"
LOAN_FEE_ALLOWANCE = 100_000
RAW_PER_CENT = 10_000
LAMPORTS = 10**9


def verify(raw):
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise ValueError("The recorded lending accounts changed")
    data = json.loads(raw)
    if data["genesis"] != "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d" or data["addresses"] != [MARKET, SOL_RESERVE, USDC_RESERVE]:
        raise ValueError("Unexpected lending market")
    m, s, u = data["accounts"]["value"]
    market = credit.read_program_account(m, "f6723262489d1c78", 4664)
    if market[122] or market[124]:
        raise ValueError("Market borrowing is paused")
    sol, usd = (credit.read_program_account(a, "2bf2ccca1af73b7f", 8624) for a in (s, u))
    uint = credit.uint
    for account, mint, decimals in ((sol, credit.SOL, 9), (usd, reserve.USDC, 6)):
        if (account[32:64] != bytes(Pubkey.from_string(MARKET)) or account[128:160] != bytes(mint)
                or uint(account, 272, 8) != decimals or account[4856] or account[4864]
                or uint(account, 5800, 8) or not 0 <= data["block_time"] - uint(account, 264, 8) <= 300):
            raise ValueError("Lending asset, permissions or recorded price are unsupported")
    initial, liquidation = sol[4872:4874]
    if (sol[5500] or not 0 < initial < liquidation <= 100 or liquidation <= 10 or usd[5501]
            or uint(usd, 6968, 8) or any(usd[5776:5792]) or usd[4865] != 1 or uint(usd, 5008, 8) < 100):
        raise ValueError("Unsupported loan configuration")
    borrowed = credit.ceiling(Decimal(uint(usd, 232)) / credit.UNIT)
    daily_cap = int.from_bytes(usd[5448:5456], "little", signed=True)
    drawn_today = int.from_bytes(usd[5456:5464], "little", signed=True)
    room = min(uint(usd, 224, 8), uint(usd, 5024, 8) - borrowed,
               uint(usd, 5504, 8) - uint(usd, 6704, 8), daily_cap - max(0, drawn_today))
    sol_supply_upper = uint(sol, 224, 8) + credit.ceiling(Decimal(uint(sol, 232)) / credit.UNIT)
    return dict(slot=data["accounts"]["context"]["slot"], apr=credit.configured_rate(usd),
                min_value=Decimal(uint(market, 3224)) / credit.UNIT, max_value=uint(market, 152, 8),
                sol_price=Decimal(uint(sol, 248)) / credit.UNIT, usd_price=Decimal(uint(usd, 248)) / credit.UNIT,
                factor=uint(usd, 5008, 8), initial=initial, liquidation=liquidation,
                origination=uint(usd, 4896, 8), credit_raw=max(0, room),
                deposit_room=max(0, uint(sol, 5016, 8) - sol_supply_upper))


@cache
def saved():
    return verify(EVIDENCE.read_bytes())


def loan_schedule(limit, low, lending):
    rate = lending["apr"]
    accrual = (2 * rate * 30 / 365).exp()
    coin = lending["usd_price"] * lending["factor"] / 100
    downside = min(lending["sol_price"], Decimal(low) / 100)
    for draw in range(limit + 1):
        raw = draw * RAW_PER_CENT
        origination = ceil_div(raw * lending["origination"], 2**60)
        debt = raw + origination
        value = debt * lending["usd_price"] / 10**6
        if debt > lending["credit_raw"]:
            yield None
            continue
        if draw and (not downside or value < lending["min_value"]
                     or value * lending["factor"] / 100 > lending["max_value"]):
            yield None
            continue
        payoff = credit.ceiling(debt * accrual)
        pledge = 0
        if draw:
            opening = debt / Decimal(10**6) * coin * 100 / (lending["sol_price"] * lending["initial"])
            stressed = payoff / Decimal(10**6) * coin * 100 / (downside * (lending["liquidation"] - 10))
            pledge = credit.ceiling(max(opening, stressed) * 10_000) * 100_000
        if pledge > lending["deposit_room"]:
            yield None
            continue
        yield pledge, ceil_div(payoff, RAW_PER_CENT)


def compare(plan, costs, stable, cash, wallet_cap, native_available, fee, rent, lending, history=(), *, offer_terms=None):
    bill, reference = plan["bill_cents"], plan["reference_cents"]
    low, high = plan["low_price_cents"], plan["high_price_cents"]
    own = min(bill, cash)
    vendor = bill - own
    gap = max(0, vendor - stable)
    rate = lending["apr"]
    schedule = list(loan_schedule(gap, low, lending))
    target = plan["floor_cents"]
    baseline = chosen = minimum = best = None
    for draw, terms in enumerate(schedule):
        sale = gap - draw
        if terms is None or sale >= len(costs):
            continue
        pledge, repay = terms
        restore = costs[sale] + (fee if vendor else 0) + (2 * LOAN_FEE_ALLOWANCE if draw else 0)
        opening = pledge + restore + (rent if sale else 0)
        proceeds = plan["receipt_cents"] - bill + sale + draw - repay
        payday = proceeds - ceil_div(restore * high, LAMPORTS)
        row = dict(draw_cents=draw, sale_cents=sale, pledge_lamports=pledge, repay_cents=repay,
                   restore_lamports=restore, opening_lamports=opening, opening_cents=ceil_div(opening * reference, LAMPORTS),
                   start_payday_cents=proceeds - ceil_div(restore * reference, LAMPORTS),
                   high_payday_cents=payday, low_payday_cents=proceeds - ceil_div(restore * low, LAMPORTS))
        if draw == 0:
            baseline = row
        if opening <= native_available and payday >= target and (minimum is None or opening < minimum["opening_lamports"]):
            minimum = row
        if opening <= wallet_cap:
            if best is None or payday > best["high_payday_cents"]:
                best = row
            if chosen is None and payday >= target:
                chosen = row
    history_result = None
    if history and chosen and chosen["draw_cents"] and baseline:
        differences = [ceil_div(baseline["restore_lamports"] * price, LAMPORTS)
                       - ceil_div(chosen["restore_lamports"] * price, LAMPORTS) - chosen["repay_cents"]
                       for price in history]
        history_result = dict(windows=len(history), ahead=sum(d > 0 for d in differences), ties=differences.count(0))
    combined = None
    if offer_terms is not None:
        combined = customer_mix(plan, costs, schedule, stable, cash, wallet_cap, fee, rent, *offer_terms)
    return dict(combined=combined, history=history_result, selected=chosen, best_funded=best, minimum=minimum, sell_only=baseline, cash_cents=own,
                stable_cents=min(stable, vendor), slot=lending["slot"], apr_bps=credit.ceiling(rate * 10000),
                stressed_apr_bps=credit.ceiling(2 * rate * 10000), liquidation_pct=lending["liquidation"],
                low_cents=low, high_cents=high)


def customer_mix(plan, costs, schedule, stable, cash, cap, fee, rent, menu, offer_fee, amount):
    bill, received = plan["bill_cents"], plan["receipt_cents"]
    high = plan["high_price_cents"]
    invoice = reserve_credit.INVOICE_CENTS
    if menu == "partial":
        amounts = range(min(received, invoice, customer_credit.ADVANCES[-1]) + 1)
    elif menu == "counter":
        amounts = (0, amount)
    else:
        amounts = customer_credit.MENUS[menu]
    # Each row pairs draw d with sale (gap - d). Keep integer rounding separate for each quoted leg.
    valid = np.array([t is not None and t[0] <= cap for t in schedule])
    pledged = np.array([t[0] if ok else 0 for t, ok in zip(schedule, valid)], dtype=np.int64)
    repayments = np.array([t[1] if ok else 0 for t, ok in zip(schedule, valid)], dtype=np.int64)
    last_draw = int(np.flatnonzero(valid)[-1])
    with_loan = [c + fee + 2 * LOAN_FEE_ALLOWANCE for c in costs]
    startup = np.array([c + (rent if s else 0) for s, c in enumerate(with_loan)], dtype=np.int64)
    buyback = np.array([ceil_div(c * high, LAMPORTS) for c in with_loan], dtype=np.int64)
    chosen = no_debt = None
    for early in amounts:
        discount = (offer_fee if early else 0) if menu == "counter" else customer_credit.customer_fee(early, offer_fee)
        if early + discount > min(received, invoice):
            continue
        own = min(cash, max(0, bill - early))
        vendor = max(0, bill - early - own)
        gap = max(0, vendor - stable)
        proceeds = received - bill + gap - discount
        allowance = proceeds - plan["floor_cents"]
        draw = None
        if gap < len(costs):
            restore = costs[gap] + (fee if vendor else 0)
            if restore + (rent if gap else 0) <= cap and ceil_div(restore * high, LAMPORTS) <= allowance:
                draw = 0
        if chosen is None and draw is None and allowance > 0:
            first, last = max(1, gap - len(costs) + 1), min(gap, last_draw)
            if first <= last:
                draws = slice(first, last + 1)
                sales = slice(gap - last, gap - first + 1)
                fits = (valid[draws] & (pledged[draws] + startup[sales][::-1] <= cap)
                        & (repayments[draws] + buyback[sales][::-1] <= allowance))
                if fits.any():
                    draw = first + int(fits.argmax())
        if draw is not None and (chosen is None or no_debt is None and draw == 0):
            sale = gap - draw
            pledge, repay = schedule[draw]
            restore = costs[sale] + (fee if vendor else 0) + (2 * LOAN_FEE_ALLOWANCE if draw else 0)
            row = dict(advance_cents=early, fee_cents=discount, invoice_balance_cents=invoice-early-discount,
                          cash_cents=own, stable_cents=min(vendor, stable), surplus_cents=max(0, early-bill),
                          draw_cents=draw, sale_cents=sale, pledge_lamports=pledge, repay_cents=repay,
                          opening_lamports=restore + pledge + (rent if sale else 0),
                          payday_cents=proceeds - repay - ceil_div(restore * high, LAMPORTS))
            if chosen is None:
                chosen = row
            if draw == 0 and no_debt is None:
                no_debt = row
        if chosen is not None and no_debt is not None:
            break
    return dict(plan=chosen, without_loan=no_debt)
