"""Size a day-15 payment under the remaining halves of matched Gemini paths."""
from datetime import date, timedelta
from fractions import Fraction
from math import ceil, floor

from freightos import collateral_path, mainnet_loan, pledge_topup

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


def prior_windows(path, price, annual_rate):
    traces = {start: trace for start, trace in sorted(path["traces"].items()) if MIDPOINT in dict(trace)}
    folds = []
    for start, trace in traces.items():
        cutoff = date.fromisoformat(start) - timedelta(days=30)
        earlier = {key: value for key, value in traces.items() if date.fromisoformat(key) < cutoff}
        if earlier:
            training, count = checkpoints({"traces": earlier}, price, annual_rate)
            later, _ = checkpoints({"traces": {start: trace}}, price, annual_rate)
            folds.append((start, max(earlier), count, training, later))
    return folds


def quote(row, budget, marks, windows, terms, folds=(), drop_bps=0, *, topup_limit=0, wallet_spare=0):
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

    maturity_growth = marks[-1][3]
    history = marks
    extra = []
    if drop_bps:
        move = Fraction(10_000 - drop_bps, 10_000)
        exact_price = current_price * move
        # For any path above this floor, the largest admissible debt occurs at maturity.
        extra = [(60, floor(exact_price) or exact_price, "driver", maturity_growth, move)]
        marks = [*history, *extra]

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
    if extra:
        base["downside"] = dict(drop_bps=drop_bps, floor_cents=floor(extra[0][1]))
    if not base["eligible"]:
        return base

    # Invert the rounded debt limit at every checkpoint, then keep the binding limit.
    def payment_for(scenario, backing=pledge):
        limit, index = min((floor(floor(target * price * backing / (conversion * pledge)) / growth), i)
                           for i, (_, price, _, growth, _) in enumerate(scenario))
        required = max(0, ceil(Fraction(debt - limit, CENT)))
        return (full_payoff if required > partial_limit else required), index

    def peak(raw, scenario, backing=pledge):
        return max(ceil(raw * growth) * conversion * pledge / (price * backing) for _, price, _, growth, _ in scenario)

    needed, binding = payment_for(marks)
    paid = min(needed, spendable)
    left = max(0, debt - paid * CENT)
    step, price, start, _, _ = marks[binding]
    trials = []
    for test_start, last_start, count, training, later in folds:
        earlier_need, _ = payment_for([*training, *extra])
        early_payment = min(earlier_need, spendable)
        observed_peak = peak(max(0, debt - early_payment * CENT), later)
        trial = dict(start=test_start, last_training_start=last_start, prior_windows=count,
                           payment_cents=early_payment, peak_bps=ceil(observed_peak),
                           missed=observed_peak > target, reached_liquidation=observed_peak >= threshold)
        if extra:
            history_need, _ = payment_for(training)
            history_cash = min(history_need, spendable)
            history_peak = peak(max(0, debt - history_cash * CENT), later)
            trial.update(history_cash_cents=history_cash, history_missed=history_peak > target,
                         history_liquidation=history_peak >= threshold)
        trials.append(trial)

    if extra:
        history_need, _ = payment_for(history)
        base["downside"].update(history_needed_cents=history_need, history_payment_cents=min(history_need, spendable))
    remaining = max(0, debt - spendable * CENT)
    base["cash_floor_cents"] = ceil(ceil(remaining * maturity_growth) * conversion / target)
    if topup_limit:
        minimum_opening = floor(debt * conversion * pledge / (threshold * current_price)) + 1
        def allocate(scenario):
            return pledge_topup.allocate(pledge, topup_limit, wallet_spare, terms, minimum_opening, spendable,
                                         lambda backing: payment_for(scenario, backing)[0])
        topup = allocate(marks)
        base["topup"] = topup
        if topup["eligible"]:
            cash = topup["payment_cents"]
            residual = max(0, debt - cash * CENT)
            topup.update(unused_cents=budget-cash, short_cents=max(0, topup["needed_cents"]-budget),
                         cash_kept_cents=paid-cash, peak_bps=ceil(peak(residual, marks, topup["represented_lamports"])),
                         payday_cents=ceil(Fraction(ceil(residual * maturity_growth), CENT)))
            later_trials = []
            for test_start, _, _, training, later in folds:
                early = allocate([*training, *extra])
                early_cash = early["payment_cents"]
                later_peak = peak(max(0, debt-early_cash*CENT), later, early["represented_lamports"])
                later_trials.append(dict(start=test_start, payment_cents=early_cash, deposit_lamports=early["deposit_lamports"],
                                         missed=later_peak > target, reached_liquidation=later_peak >= threshold))
            topup["later_trials"] = later_trials
    return {**base, "needed_cents": needed, "payment_cents": paid, "unused_cents": budget-paid,
            "short_cents": max(0, needed-budget), "payday_cents": ceil(Fraction(ceil(left * marks[-1][3]), CENT)),
            "wait_peak_bps": ceil(peak(debt, marks)), "paid_peak_bps": ceil(peak(left, marks)), "later_trials": trials, "binding_hours": step*12,
            "binding_price_cents": floor(price), "binding_start": start}
