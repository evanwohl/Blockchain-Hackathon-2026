"""Compare a protected SOL sale with a USDC draw against SOL for the same fuel bill."""
from bisect import bisect_left
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from functools import cache
from pathlib import Path

import numpy as np
from solders.pubkey import Pubkey

from freightos import collateral_path, customer_credit, freight_credit as credit, reserve, reserve_credit
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
    return dict(slot=data["accounts"]["context"]["slot"], collateral_farm=str(Pubkey.from_bytes(sol[64:96])),
                debt_farm=str(Pubkey.from_bytes(usd[96:128])), apr=credit.configured_rate(usd),
                min_value=Decimal(uint(market, 3224)) / credit.UNIT, max_value=uint(market, 152, 8),
                sol_price=Decimal(uint(sol, 248)) / credit.UNIT, usd_price=Decimal(uint(usd, 248)) / credit.UNIT,
                factor=uint(usd, 5008, 8), initial=initial, liquidation=liquidation,
                flash_fee_sf=uint(usd, 4904, 8), flash_available_raw=uint(usd, 224, 8),
                origination=uint(usd, 4896, 8), credit_raw=max(0, room),
                deposit_room=max(0, uint(sol, 5016, 8) - sol_supply_upper))


@cache
def saved():
    return verify(EVIDENCE.read_bytes())


SETUP_EVIDENCE = EVIDENCE.with_name("borrower_setup.json")


@cache
def borrower_setup(*, returning=False):
    raw = SETUP_EVIDENCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "8283e41a0eb0a4e94007a21908637695da07549b062c6acd9fa0d5e3e5b64b41":
        raise ValueError("The recorded account deposits changed")
    evidence = json.loads(raw)
    lending = saved()
    if (evidence["collateral_farm"] != lending["collateral_farm"] or evidence["debt_farm"] != lending["debt_farm"]
            or lending["debt_farm"] != str(Pubkey.default())):
        raise ValueError("The account setup differs from the recorded lending route")
    accounts = evidence["accounts"]
    obligation = accounts["obligation"]["lamports"]
    retained = 0 if returning else accounts["profile"]["lamports"] + accounts["collateral_farm_user"]["lamports"]
    mark = lending["sol_price"] * 100 / LAMPORTS

    def value_cents(raw):
        return int((raw * mark).to_integral_value(rounding=ROUND_HALF_UP))

    opening = obligation + retained + LOAN_FEE_ALLOWANCE
    return dict(returning=returning, refunded_lamports=obligation, retained_lamports=retained, setup_fee_lamports=LOAN_FEE_ALLOWANCE,
                deposit_lamports=obligation + retained, opening_value_cents=credit.ceiling(opening * mark),
                refund_value_cents=value_cents(obligation), retained_value_cents=value_cents(retained),
                setup_fee_value_cents=value_cents(LOAN_FEE_ALLOWANCE),
                slot=evidence["through_slot"])


def loan_schedule(limit, low, lending, marks=()):
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
            if marks:
                peak, _ = collateral_path.peak(debt, marks)
                stressed = max(stressed, peak * coin / (100 * (lending["liquidation"] - 10)))
            pledge = credit.ceiling(max(opening, stressed) * 10_000) * 100_000
        if pledge > lending["deposit_room"]:
            yield None
            continue
        yield pledge, ceil_div(payoff, RAW_PER_CENT)


def payday_at(row, high, price):
    restore = row["restore_lamports"]
    return row["payday_cents"] + ceil_div(restore * high, LAMPORTS) - ceil_div(restore * price, LAMPORTS)


def secured_schedule(lending, native_available, gap, low, marks):
    max_draw = int(Decimal(native_available) * lending["sol_price"] * lending["initial"]
                   / (LAMPORTS * lending["usd_price"]) * 100 / lending["factor"])
    return list(loan_schedule(min(gap, max_draw), low, lending, marks))


def compare(plan, costs, stable, cash, wallet_cap, native_available, fee, rent, lending, history=(), *, offer_terms=None, path=None, setup=None, repeat=False, schedule=None):
    bill, reference = plan["bill_cents"], plan["reference_cents"]
    low, high = plan["low_price_cents"], plan["high_price_cents"]
    own = min(bill, cash)
    vendor = bill - own
    gap = max(0, vendor - stable)
    rate = lending["apr"]
    marks = collateral_path.barriers(path, lending["sol_price"], rate * 2) if path else []
    if schedule is None:
        schedule = secured_schedule(lending, native_available, gap, low, marks)
    target = plan["floor_cents"]
    loan_spend = 2 * LOAN_FEE_ALLOWANCE
    close_refund = 0
    if setup:
        loan_spend += setup["retained_lamports"] + setup["setup_fee_lamports"]
        close_refund = setup["refunded_lamports"]
    baseline = chosen = minimum = best = None
    for draw, terms in enumerate(schedule):
        sale = gap - draw
        if terms is None or sale >= len(costs):
            continue
        pledge, repay = terms
        restore = costs[sale] + (fee if vendor else 0) + (loan_spend if draw else 0)
        opening = pledge + restore + (rent if sale else 0) + (close_refund if draw else 0)
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
    combined = repeat_plan = None
    if offer_terms is not None:
        combined = customer_mix(plan, costs, schedule, stable, cash, wallet_cap, fee, rent, *offer_terms, loan_spend=loan_spend, close_refund=close_refund, show_loan=bool(setup))
        if repeat:
            next_setup = borrower_setup(returning=True)
            next_mix = customer_mix(plan, costs, schedule, stable, cash, wallet_cap, fee, rent, *offer_terms,
                                    loan_spend=2 * LOAN_FEE_ALLOWANCE + next_setup["setup_fee_lamports"],
                                    close_refund=next_setup["refunded_lamports"])
            repeat_plan = next_mix["most_cash"]
        for row in combined.values():
            if row:
                remaining = wallet_cap - row["opening_lamports"]
                row["available_lamports"] = remaining
                row["available_sol_value_cents"] = int(remaining * lending["sol_price"] * 100 / LAMPORTS)
        payday_plan, debt_free = combined["most_cash"], combined["most_cash_without_loan"]
        if payday_plan and debt_free:
            def balances(price):
                return dict(price_cents=price, allowed_cents=payday_at(payday_plan, high, price),
                            debt_free_cents=payday_at(debt_free, high, price))
            saved_sol = debt_free["restore_lamports"] - payday_plan["restore_lamports"]
            if payday_plan["draw_cents"] and saved_sol > 0:
                opening = balances(0)
                carry = opening["debt_free_cents"] - opening["allowed_cents"]
                # The difference of two rounded-up buybacks is between floor and ceil of the raw difference.
                combined["switch_prices"] = dict(
                    borrowing_from=max(0, ceil_div((carry + 1) * LAMPORTS, saved_sol)),
                    debt_free_through=(carry - 1) * LAMPORTS // saved_sol if carry >= 1 else None,
                )
            observations = [balances(price) for price in history]
            combined["prices"] = [balances(price) for price in dict.fromkeys((low, reference, high))]
            combined["history"] = dict(windows=len(observations),
                                       ahead=sum(v["allowed_cents"] > v["debt_free_cents"] for v in observations),
                                       ties=sum(v["allowed_cents"] == v["debt_free_cents"] for v in observations))
    if path:
        for row in (chosen, minimum, combined and combined["plan"], combined and combined["most_cash"], combined and combined["keep_sol"]):
            if not row or not row["draw_cents"]:
                continue
            raw = row["draw_cents"] * RAW_PER_CENT
            debt = raw + ceil_div(raw * lending["origination"], 2**60)
            row["path_check"] = collateral_path.replay(path, marks, debt, row["pledge_lamports"], lending)
    coverage = {key: path[key] for key in ("windows", "observations", "omitted", "max_gap_hours")} if path else None
    return dict(setup=setup, repeat_plan=repeat_plan, path_coverage=coverage, combined=combined, history=history_result, selected=chosen, best_funded=best, minimum=minimum, sell_only=baseline, cash_cents=own,
                stable_cents=min(stable, vendor), slot=lending["slot"], apr_bps=credit.ceiling(rate * 10000),
                stressed_apr_bps=credit.ceiling(2 * rate * 10000), liquidation_pct=lending["liquidation"],
                low_cents=low, high_cents=high)


def _ordered_draws(valid, pledged, repayments, startup, buyback):
    positive = np.flatnonzero(valid[1:]) + 1
    if len(positive) < 2 or len(startup) < 3 or positive[-1] - positive[0] + 1 != len(positive):
        return None
    block = slice(positive[0], positive[-1] + 1)
    if np.diff(pledged[block]).min() <= np.diff(startup[1:]).max():
        return None
    if np.diff(repayments[block]).max() > np.diff(buyback[1:]).min():
        return None
    return int(positive[0])


def _boundary_draws(first, last, gap, cap, allowance, pledged, repayments, startup, buyback):
    # For positive sales, opening SOL strictly increases and payoff plus buyback never increases.
    # The zero-sale endpoint drops refundable rent and must be compared separately.
    candidates = [gap] if gap == last else []
    span = range(first, min(last, gap - 1) + 1)
    end = bisect_left(span, True, key=lambda d: pledged[d] + startup[gap - d] > cap)
    if end:
        funded = span[:end]
        def cost(draw):
            return repayments[draw] + buyback[gap - draw]
        best = cost(funded[-1])
        candidates.append(funded[bisect_left(funded, True, key=lambda d: cost(d) <= best)])
        if best <= allowance:
            candidates.append(funded[bisect_left(funded, True, key=lambda d: cost(d) <= allowance)])
    return np.array(sorted(set(candidates)), dtype=np.intp)


def funding_row(plan, net, discount, cash, stable, draw, terms, costs, fee, rent, loan_spend, close_refund):
    bill, received, high = plan["bill_cents"], plan["receipt_cents"], plan["high_price_cents"]
    invoice = plan.get("invoice_cents", reserve_credit.INVOICE_CENTS)
    own = min(cash, max(0, bill - net))
    vendor = max(0, bill - net - own)
    dollars = min(vendor, stable)
    sale = vendor - dollars - draw
    pledge, repay = terms
    restore = costs[sale] + (fee if vendor else 0) + (loan_spend if draw else 0)
    return dict(advance_cents=net, fee_cents=discount, invoice_balance_cents=invoice-net-discount,
                cash_cents=own, stable_cents=dollars, surplus_cents=max(0, net-bill), draw_cents=draw,
                sale_cents=sale, pledge_lamports=pledge, repay_cents=repay,
                restore_lamports=restore, opening_lamports=restore + pledge + (rent if sale else 0) + (close_refund if draw else 0),
                payday_cents=received - bill + sale + draw - discount - repay - ceil_div(restore * high, LAMPORTS))


def customer_mix(plan, costs, schedule, stable, cash, cap, fee, rent, menu, offer_fee, amount, *,
                 loan_spend=2 * LOAN_FEE_ALLOWANCE, close_refund=0, show_loan=False):
    bill, received = plan["bill_cents"], plan["receipt_cents"]
    high = plan["high_price_cents"]
    invoice = plan.get("invoice_cents", reserve_credit.INVOICE_CENTS)
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
    with_loan = [c + fee + loan_spend for c in costs]
    startup = np.array([c + close_refund + (rent if s else 0) for s, c in enumerate(with_loan)], dtype=np.int64)
    buyback = np.array([ceil_div(c * high, LAMPORTS) for c in with_loan], dtype=np.int64)
    ordered_from = _ordered_draws(valid, pledged, repayments, startup, buyback)
    chosen = no_debt = most_cash = best_debt_free = None
    least_opening = least_debt_free = None
    every_loan_dominated = True
    inf = np.iinfo(np.int64).max

    def describe(net, draw):
        discount = offer_fee if menu == "counter" and net else customer_credit.customer_fee(net, offer_fee)
        return funding_row(plan, net, discount, cash, stable, draw, schedule[draw], costs, fee, rent, loan_spend, close_refund)

    for early in amounts:
        discount = offer_fee if menu == "counter" and early else customer_credit.customer_fee(early, offer_fee)
        if early + discount > min(received, invoice):
            continue
        vendor = max(0, bill - early - cash)
        gap = max(0, vendor - stable)
        proceeds = received - bill + gap - discount
        allowance = proceeds - plan["floor_cents"]
        full_sale_fits = False
        if gap < len(costs):
            restore = costs[gap] + (fee if vendor else 0)
            opening = restore + (rent if gap else 0)
            if opening <= cap:
                full_sale_fits = True
                cost = ceil_div(restore * high, LAMPORTS)
                key = (cost - proceeds, 0, early)
                best_debt_free = key if best_debt_free is None else min(best_debt_free, key)
                if cost <= allowance:
                    wallet_key = (opening, *key)
                    if least_debt_free is None or wallet_key < least_debt_free:
                        least_debt_free = wallet_key
                    if no_debt is None:
                        no_debt = early
                    if chosen is None:
                        chosen = (early, 0)
        first, last = max(1, gap - len(costs) + 1), min(gap, last_draw)
        if first > last:
            continue
        if ordered_from is not None and last - first > 32:
            draws = _boundary_draws(max(first, ordered_from), last, gap, cap, allowance, pledged, repayments, startup, buyback)
            sales = gap - draws
            numbers = draws
        else:
            draws = slice(first, last + 1)
            sales = slice(gap - first, gap - last - 1 if gap > last else None, -1)
            numbers = range(first, last + 1)
        opening = pledged[draws] + startup[sales]
        funded = valid[draws] & (opening <= cap)
        if not funded.any():
            continue
        # A funded full sale spending no more SOL than the fixed loan outlay also avoids every positive payoff.
        if show_loan and (not full_sale_fits or costs[gap] > loan_spend):
            every_loan_dominated = False
        cost = np.where(funded, repayments[draws] + buyback[sales], inf)
        index = int(cost.argmin())
        key = (int(cost[index]) - proceeds, int(numbers[index]), early)
        most_cash = key if most_cash is None else min(most_cash, key)
        if cost[index] <= allowance:
            fits = cost <= allowance
            if chosen is None:
                chosen = (early, int(numbers[fits.argmax()]))
            minimum = int(np.min(opening, where=fits, initial=inf))
            if least_opening is None or minimum <= least_opening[0]:
                tied = np.flatnonzero(fits & (opening == minimum))
                best_index = int(tied[cost[tied].argmin()])
                wallet_key = (minimum, int(cost[best_index]) - proceeds, int(numbers[best_index]), early)
                if least_opening is None or wallet_key < least_opening:
                    least_opening = wallet_key
    best = min((k for k in (most_cash, best_debt_free) if k is not None), default=None)
    wallet = min((k for k in (least_opening, least_debt_free) if k is not None), default=None)
    borrowed = describe(most_cash[2], most_cash[1]) if show_loan and most_cash else None
    if borrowed:
        borrowed["all_loans_dominated"] = every_loan_dominated
    return dict(best_loan=borrowed,
                keep_sol=describe(wallet[3], wallet[2]) if wallet else None,
                keep_sol_without_loan=describe(least_debt_free[3], 0) if least_debt_free else None,
                plan=describe(*chosen) if chosen else None,
                without_loan=describe(no_debt, 0) if no_debt is not None else None,
                most_cash=describe(best[2], best[1]) if best else None,
                most_cash_without_loan=describe(best_debt_free[2], 0) if best_debt_free else None)
