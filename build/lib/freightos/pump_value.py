"""Price the portion of an oil payout a driver could spend on an uncovered fill."""

from bisect import bisect_right
from datetime import date, timedelta
from fractions import Fraction
from math import isqrt

from freightos import backup, fill_replay

WEEK = timedelta(days=7)


def diesel_activity(prices, end):
    levels = [prices.get((end - i * WEEK).isoformat()) for i in range(5)]
    if any(price is None for price in levels):
        return None
    return sum(abs(a - b) for a, b in zip(levels, levels[1:]))


def predict(history, activity):
    pooled = Fraction(sum(value for _, value in history), len(history))
    distances = sorted((abs(activity - level), value) for level, value in history)
    radius = distances[isqrt(len(history)) - 1][0]
    neighbors = [value for distance, value in distances if distance <= radius]
    return (sum(neighbors) + 4 * pooled) / (len(neighbors) + 4), pooled


def evaluate(samples, offers, plan, prices, next_fill, as_of):
    if not samples or not plan["legs"]:
        return None
    gaps, payouts = fill_replay.pump_gaps(samples, offers, plan)
    needs = [row[1] for row in samples]
    relief = [need - gap for need, gap in zip(needs, gaps)]
    count = len(samples)
    premium = plan["upfront_cents"]
    result = {"weeks": count, "through": samples[-1][2], "premium_cents": premium,
              "short_weeks": sum(need > 0 for need in needs),
              "paid_weeks": sum(payouts[row[0]] > 0 for row in samples),
              "helped_weeks": sum(value > 0 for value in relief),
              "mean_ceiling_cents": sum(relief) // count,
              "tail_ceiling_cents": (fill_replay.tail_sum(needs) - fill_replay.tail_sum(gaps)) // count}
    history, dates, records = [], [], []
    for row, value in zip(samples, relief):
        day = date.fromisoformat(row[2])
        through = day - 2 * WEEK
        activity = diesel_activity(prices, through)
        if activity is None:
            continue
        eligible = bisect_right(dates, through)
        if day - WEEK >= backup.EVALUATION_START and eligible >= backup.WINDOW:
            local, pooled = predict(history[:eligible], activity)
            records.append((local, pooled, value, day.isoformat(), dates[eligible - 1].isoformat()))
        dates.append(day)
        history.append((activity, value))
    if not records:
        return result

    def performance(index):
        estimates = [row[index] for row in records]
        values = [row[2] for row in records]
        return {"buys": sum(est > premium for est in estimates),
                "net_relief_cents": sum(v - premium for est, v in zip(estimates, values) if est > premium),
                "mse": float(sum((est - v)**2 for est, v in zip(estimates, values)) / len(values))}

    result["validation"] = {"weeks": len(records), "local": performance(0), "pooled": performance(1),
                            "always_net_cents": sum(row[2] - premium for row in records)}
    result["weekly_checks"] = [
        {"fill": day, "training_through": last, "covered_cents": value, "local_ceiling_cents": int(local),
         "pooled_ceiling_cents": int(pooled), "local_buys": local > premium, "pooled_buys": pooled > premium}
        for local, pooled, value, day, last in records]
    through = next_fill - 2 * WEEK
    eligible = bisect_right(dates, through)
    activity = diesel_activity(prices, through)
    if (next_fill - WEEK <= as_of <= next_fill and activity is not None and eligible >= backup.WINDOW
            and dates[eligible - 1] == through):
        local, pooled = predict(history[:eligible], activity)
        result["estimate"] = {"through": through.isoformat(), "weeks": eligible,
                              "local_cents": int(local), "pooled_cents": int(pooled)}
    return result
