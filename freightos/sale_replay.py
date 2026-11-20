"""Freeze each sale decision before replaying its later price and collateral earnings."""
from fractions import Fraction
from math import ceil, floor

from freightos import mainnet_loan, midtrip, partial_sale, pledge_yield
from freightos.payoff_budget import modeled_input

SOL = mainnet_loan.LAMPORTS


def peak_debt(debt, payment, backing, terms, later):
    remaining = max(0, debt - payment * midtrip.CENT)
    factor = Fraction(terms["usd_price"]) * terms["factor"] * 10**7 / backing
    return max(ceil(remaining * growth) * factor / price for _, price, _, growth, _ in later)


def evaluate(row, budget, terms, path, price, drop, topup_limit, spare, folds, pools, strict, loss_limit):
    rates = pledge_yield.saved()
    stress = Fraction(price * (10_000-drop), 10_000) if drop else None
    trials = []
    for start, last, count, training, later in folds:
        trial = dict(start=start, last_training_start=last, prior_windows=count, action="no_sale_plan")
        trials.append(trial)
        plan = midtrip.quote(row, budget, training, count, terms, drop_bps=drop,
                             topup_limit=topup_limit, wallet_spare=spare)
        topup = plan.get("topup", {})
        sale = topup.get("sale")
        if not sale:
            continue
        prior = {key: trace for key, trace in path["traces"].items() if key <= last and 30 in dict(trace)}
        carry = pledge_yield.compare(topup, sale, row["pledge_lamports"], terms, {"traces": prior}, price, stress, strict, loss_limit=loss_limit)
        cap = carry["cap_lamports"]
        spend = modeled_input(pools, sale["output_cents"], price)
        filled = spend is not None and spend <= cap
        pledge_risk = peak_debt(plan["day15_debt_raw"], topup["payment_cents"], topup["represented_lamports"], terms, later)
        chosen_risk = peak_debt(plan["day15_debt_raw"], sale["payment_cents"], topup["initial_represented_lamports"],
                                terms, later) if filled else pledge_risk
        trial.update(action="sale" if filled else "pledge", cap_lamports=cap, input_lamports=spend,
                     output_cents=sale["output_cents"], cash_cents=topup["payment_cents"],
                     added_lamports=topup["deposit_limit_lamports"], peak_bps=ceil(chosen_risk),
                     pledge_peak_bps=ceil(pledge_risk), cushion_missed=chosen_risk > plan["target_bps"])
        if max(chosen_risk, pledge_risk) >= plan["limit_bps"]:
            trial["difference_cents"] = None
        elif filled:
            claims = pledge_yield.positions(topup, row["pledge_lamports"], terms)
            _, excess, mark = pledge_yield.finish(start, dict(path["traces"][start]), price, claims, topup["deposit_lamports"], rates)
            trial["difference_cents"] = floor(topup["payday_cents"] - sale["payday_cents"] - (spend + excess) * mark / SOL)
        else:
            trial["difference_cents"] = 0
        if not filled and loss_limit is not None and spend is not None:
            split = partial_sale.choose(row, plan, {"traces": prior}, terms, training, pools, loss_limit)
            if split:
                risk = peak_debt(plan["day15_debt_raw"], split["payment_cents"], split["represented_lamports"], terms, later)
                score = None
                if max(risk, pledge_risk) < plan["limit_bps"]:
                    _, reference = pledge_yield.positions(topup, row["pledge_lamports"], terms)
                    _, mixed = pledge_yield.positions(split, row["pledge_lamports"], terms)
                    _, gain = pledge_yield.share_growth(start, rates)
                    path_points = dict(path["traces"][start])
                    mark = price * path_points[60] / path_points[30]
                    missed = floor(reference*gain) - floor(mixed*gain) - topup["deposit_lamports"] + split["deposit_lamports"]
                    score = floor(topup["payday_cents"] - split["payday_cents"] - (missed + split["modeled_input_lamports"])*mark/SOL)
                trial["split_option"] = dict(output_cents=split["output_cents"], added_lamports=split["deposit_limit_lamports"],
                                             difference_cents=score, cushion_missed=risk > plan["target_bps"])
    return trials
