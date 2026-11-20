"""Size same-expiry Gemini positions against the driver's fuel budget."""

import gzip
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import cache
from itertools import accumulate
from math import ceil

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from freightos import backup, diesel_model, fill_replay, fuel_cover, fuel_update, fuel_volatility, payday, payday_price, pump_value, regional_diesel
from hedging.quick_pay import integer

SNAPSHOT = fuel_cover.SNAPSHOT.with_name("trip_cover.json.gz")
EXAMPLE = {"gallons": 100, "pump_mills": 3999, "budget_cents": 40000}


def validate(trip):
    if not integer(trip.get("gallons"), "gallons", 10000) or not integer(trip.get("pump_mills"), "diesel price", 99999):
        raise ValueError("Enter a positive gallon count and posted diesel price")
    integer(trip.get("budget_cents"), "fuel budget", 100_000_000)
    if "spend_limit_cents" in trip:
        integer(trip["spend_limit_cents"], "oil protection limit", 100_000_000)
    if "pump_cash_cents" in trip:
        integer(trip["pump_cash_cents"], "extra cash at the pump", 100_000_000)
    if "diesel_model" in trip and (trip["diesel_model"] != "recent" or "region" not in trip):
        raise ValueError("Choose a diesel region to use its recent price pattern")
    if "region" in trip and (not isinstance(trip["region"], str) or trip["region"] not in regional_diesel.REGIONS):
        raise ValueError("Choose a supported diesel region")


def contracts(market):
    _, _, _, window, key = fuel_update.watch_terms()
    original = fuel_update.instruments(window[key])
    event = market["events"][key]["event"]
    return [c for c in event["contracts"] if not c["isCombo"] and
            (c["instrumentSymbol"], c["id"], c["strike"]["type"], c["strike"]["value"], c["expiryDate"]) in original
            and c["strike"]["type"] == "above"]


def capture():
    market = fuel_update.capture()
    fuel = fuel_update.compare(market)
    active = [c for c in contracts(market) if c["marketState"] == "open" and c["prices"].get("buy", {}).get("yes")]

    def fetch(contract):
        symbol = contract["instrumentSymbol"]
        try:
            stamp, levels = fuel_update.fetch_json(fuel_cover.BOOK + symbol)
            return symbol, {"reported_at": stamp, "levels": levels}
        except (OSError, ValueError):
            return symbol, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        books = dict(pool.map(fetch, active)) if fuel["status"] == "active" else {}
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "market": market, "books": books}


def matching_bid(levels, sell, ask):
    if levels == [] and not sell:
        return 0
    if not levels or not sell:
        return None
    try:
        bids = [(Decimal(q["price"]), Decimal(q["amount"])) for q in levels]
        if any(not p.is_finite() or not size.is_finite() or not 0 < p <= ask or size <= 0 for p, size in bids):
            return None
        best = max(p for p, _ in bids)
        return int(best * 100) if best == Decimal(sell) and best * 100 % 1 == 0 else None
    except (KeyError, TypeError, InvalidOperation):
        return None


def top_offers(snapshot, fuel, rows):
    checked = datetime.fromisoformat(snapshot["checked_at"])

    def fresh(stamp):
        return -2 <= (checked - datetime.fromisoformat(stamp)).total_seconds() <= 120

    if (fuel["status"] != "active" or not fresh(fuel["quote_at"]) or checked >= datetime.fromisoformat(fuel["expiry"])):
        return []
    offers = []
    for c in rows:
        symbol = c["instrumentSymbol"]
        book = snapshot["books"].get(symbol)
        ask = c["prices"].get("buy", {}).get("yes")
        if (not book or not fresh(book["reported_at"]) or not ask or c["status"] != "active"
                or c["marketState"] != "open" or datetime.fromisoformat(c["effectiveDate"]) > checked):
            continue
        levels = [(Decimal(q["price"]), Decimal(q["amount"])) for q in book["levels"]["asks"]]
        if not levels:
            continue
        price = min(p for p, _ in levels)
        depth = sum(q for p, q in levels if p == price)
        if (any(not 0 < p < 1 or q <= 0 for p, q in levels) or price != Decimal(ask)
                or Decimal(c["quantityMinimum"]) != Decimal(".01") or Decimal(c["quantityIncrement"]) != Decimal(".01")
                or price * 100 % 1 or depth * 100 % 1):
            continue
        bid = matching_bid(book["levels"].get("bids"), c["prices"].get("sell", {}).get("yes"), price)
        offers.append({"symbol": symbol, "strike": c["strike"]["value"], "price": int(price * 100),
                       "depth": int(depth * 100), "bid": bid})
    return sorted(offers, key=lambda o: Decimal(o["strike"]))


def cost(lots, offer):
    p = offer["price"]
    return (lots * p + 99) // 100, (lots * 7 * p * (100 - p) + 999999) // 1000000


def basket_quote(legs, limit):
    if limit is None:
        return None
    cuts = {0}
    for leg in legs:
        price = leg["price"]
        cuts.update((10000 * ticks + price - 1) // price for ticks in range(1, price))
    for cut in sorted(cuts):
        quotes = []
        total = 0
        for leg in legs:
            price = max(1, (leg["price"] * (10000 - cut) + 9999) // 10000)
            premium, fee = cost(leg["lots"], {"price": price})
            quotes.append({"strike": leg["strike"], "lots": leg["lots"], "ask": leg["price"],
                           "target": price, "premium_cents": premium, "fee_cents": fee})
            total += premium + fee
        if total <= limit:
            return {"cut_bps": cut, "all_in_cents": total, "legs": quotes}
    return None


def residual_floor(offers, gaps):
    floor = gaps[0]
    payout = 0
    for offer, need in zip(offers, gaps[1:]):
        payout += offer["depth"]
        floor = max(floor, need - payout)
    return floor


def funding_risk_problem(offers, gaps, capacity, rows, bounds, caps):
    n = len(offers)
    first_flag = len(caps)
    intercept = first_flag + len(gaps)
    upper = intercept + 1
    lower = upper + n
    columns = lower + n
    rows = [np.pad(row, (0, columns - first_flag)) for row in rows]
    for i, need in enumerate(gaps):
        row = np.zeros(columns)
        row[:i] = -1
        row[first_flag + i] = -max(0, need - capacity)
        rows.append(row)
        bounds.append(capacity - need)
        row = np.zeros(columns)
        row[first_flag + i] = 1
        row[intercept] = -1
        row[upper:upper+i] = -1
        row[lower:lower+i] = 1
        rows.append(row)
        bounds.append(0)
    objective = [0] * intercept + [100] + [o["price"] for o in offers] + [-o["bid"] for o in offers]
    caps = caps + [1] * len(gaps) + [np.inf] * (1 + 2*n)
    integrality = [1] * intercept + [0] * (1 + 2*n)
    return rows, caps, objective, integrality


def tail_problem(n, caps, rows, bounds, samples, one_late, first_fill_floor):
    positive = [i for i, (_, need, *_) in enumerate(samples) if need]
    scenarios = (fill_replay.delay_candidates(samples) if one_late else []) or [None]
    start = len(caps)
    width = 1 + len(positive)
    size = start + 1 + width*len(scenarios)
    rows = [np.pad(row, (0, size - start)) for row in rows]
    for j, delayed in enumerate(scenarios):
        threshold = start + 1 + j*width
        row = np.zeros(size)
        row[start], row[threshold] = -1, len(samples)
        row[threshold+1:threshold+width] = 10
        rows.append(row)
        bounds.append(0)
        for k, i in enumerate(positive, 1):
            band, need, *_ = samples[i]
            row = np.zeros(size)
            if i != delayed:
                row[:band] = -1
            row[threshold] = row[threshold+k] = -1
            rows.append(row)
            bounds.append(-need)
            if first_fill_floor and samples[i][2]:
                floor = np.zeros(size)
                floor[threshold] = floor[threshold+k] = -1
                rows.append(floor)
                bounds.append(-samples[i][2])
    objective = np.zeros(size)
    objective[n:3*n], objective[start] = len(samples), 1
    return rows, caps + [np.inf]*(size-start), objective, [1]*start + [0]*(size-start)


def portfolio(offers, gaps, *, fuel_cash=None, spend_limit=None, minimize_residual=False, risk_capacity=None, risk_limit=None, tail_samples=None, one_late=False, first_fill_floor=False):
    gap = gaps[-1]
    empty = {"cash_cents": gap, "upfront_cents": 0, "residual_cents": gap, "legs": []}
    n = len(offers)
    if fuel_cash is not None:
        if gap <= fuel_cash and risk_capacity is None and tail_samples is None:
            return empty
        if residual_floor(offers, gaps) > fuel_cash:
            return None
    if not n or not gap or spend_limit == 0:
        return empty if fuel_cash is None or gap <= fuel_cash else None
    # Variables are hundredths of a contract, premium cents, fee cents, and unhedged cash cents.
    rows, bounds = [], []
    for i, offer in enumerate(offers):
        p = offer["price"]
        for column, coefficient, denominator in ((n + i, p, 100), (2*n + i, 7*p*(100-p), 1000000)):
            row = np.zeros(3*n + 1)
            row[i], row[column] = coefficient, -denominator
            rows.append(row)
            bounds.append(0)
    for i, need in enumerate(gaps):
        row = np.zeros(3*n + 1)
        row[:i], row[-1] = -1, -1
        rows.append(row)
        bounds.append(-need)
    if spend_limit is not None:
        row = np.zeros(3*n + 1)
        row[n:3*n] = 1
        rows.append(row)
        bounds.append(spend_limit)
    lot_limit = max(gap, max((row[1] for row in tail_samples), default=0)) if tail_samples else gap
    caps = [min(o["depth"], lot_limit) for o in offers] + [lot_limit] * (2*n) + [gap]
    objective = [0]*n + [1]*(2*n + 1)
    if fuel_cash is not None:
        caps[-1] = min(gap, fuel_cash)
        objective[-1] = 0
    if minimize_residual:
        objective = [0]*(3*n) + [1]
    integrality = np.ones(3*n + 1)
    if risk_capacity is not None:
        rows, caps, objective, integrality = funding_risk_problem(offers, gaps, risk_capacity, rows, bounds, caps)
        if risk_limit is not None:
            rows.append(np.array(objective))
            bounds.append(risk_limit)
            objective = np.zeros(len(caps))
            objective[3*n] = 1
    if tail_samples:
        rows, caps, objective, integrality = tail_problem(n, caps, rows, bounds, tail_samples, one_late, first_fill_floor)
    constraints = LinearConstraint(rows, -np.inf, bounds)
    sol = milp(objective, integrality=integrality, bounds=Bounds(0, caps), constraints=constraints,
               options={"mip_rel_gap": 0, "time_limit": 3})
    if (risk_capacity is not None or tail_samples) and sol.status == 1:
        return None
    if sol.status == 2 and fuel_cash is not None and spend_limit is not None:
        return None
    if not sol.success:
        raise ValueError("Could not establish the minimum cash requirement")
    lots = [int(round(q)) for q in sol.x[:n]]
    prefix = 0
    residual = gaps[0]
    legs = []
    for i, (qty, offer) in enumerate(zip(lots, offers)):
        if not 0 <= qty <= caps[i]:
            raise ValueError("Position exceeds the available quantity")
        if qty:
            premium, fee = cost(qty, offer)
            legs.append({**offer, "lots": qty, "premium_cents": premium, "fee_cents": fee})
        prefix += qty
        residual = max(residual, gaps[i+1] - prefix)
    upfront = sum(leg["premium_cents"] + leg["fee_cents"] for leg in legs)
    if spend_limit is not None and upfront > spend_limit:
        raise ValueError("Repriced contracts exceed the spending limit")
    total = residual + upfront
    measured = total if fuel_cash is None else upfront
    if minimize_residual:
        measured = residual
    if risk_capacity is not None:
        weight = funding_weight(offers, gaps, lots, risk_capacity)
        if risk_limit is not None and weight > risk_limit:
            raise ValueError("Repriced hedge exceeds the funding risk limit")
        measured = weight if risk_limit is None else residual
    if tail_samples:
        payouts = list(accumulate(lots, initial=0))
        misses = [max(0, need - payouts[band]) for band, need, *_ in tail_samples]
        if first_fill_floor:
            misses = [max(row[2], miss) for row, miss in zip(tail_samples, misses, strict=True)]
        tail = fill_replay.worst_single_delay(tail_samples, misses)[0] if one_late else fill_replay.tail_sum(misses)
        measured = upfront * len(misses) + tail
    if abs(measured - sol.fun) > .001 or (fuel_cash is not None and residual > fuel_cash):
        raise ValueError("Rounded position does not reconcile with the optimum")
    return {"cash_cents": total, "upfront_cents": upfront, "residual_cents": residual, "legs": legs} if minimize_residual or fuel_cash is not None or total < gap else empty


def market_value_range(offers, changes):
    if not offers or any(o.get("bid") is None for o in offers):
        return None
    ticks = np.arange(101, dtype=np.int64)
    low = high = np.zeros_like(ticks)
    reachable = np.ones(101, dtype=bool)
    for offer, change in zip(reversed(offers), reversed(changes)):
        valid = reachable & (ticks >= offer["bid"]) & (ticks <= offer["price"])
        if not valid.any():
            return None
        value = change * ticks
        low = np.minimum.accumulate(np.where(valid, value + low, np.iinfo(np.int64).max))
        high = np.maximum.accumulate(np.where(valid, value + high, np.iinfo(np.int64).min))
        reachable = np.logical_or.accumulate(valid)
    return {"low_cents": int(low[-1]) // 100, "high_cents": -(-int(high[-1]) // 100)}


def funding_weight(offers, gaps, lots, capacity):
    payouts = accumulate(lots, initial=0)
    flags = [100 * int(need > capacity + payout) for need, payout in zip(gaps, payouts)]
    changes = [b - a for a, b in zip(flags, flags[1:])]
    mark = market_value_range(offers, changes)
    return flags[0] + mark["high_cents"] if mark else None


def protection_change(offers, gaps, capacity, selected, alternative):
    def quantities(plan):
        return {leg["strike"]: leg["lots"] for leg in plan["legs"]}

    old, new = quantities(selected), quantities(alternative)
    changes = [new.get(o["strike"], 0) - old.get(o["strike"], 0) for o in offers]
    payouts = list(accumulate(changes, initial=0))
    held = list(accumulate((old.get(o["strike"], 0) for o in offers), initial=0))
    lower = [max(0, need - payout - capacity) for need, payout in zip([0, *gaps[:-1]], held)]
    upper = [max(0, need - payout - capacity) for need, payout in zip(gaps, held)]

    def draw_mark(amounts, bound):
        differences = [b - a for a, b in zip(amounts, amounts[1:])]
        mark = market_value_range(offers, differences)
        return amounts[0] + mark[bound] if mark else None

    strikes = [None, *(o["strike"] for o in offers)]
    return {"extra_cents": alternative["upfront_cents"] - selected["upfront_cents"],
            "total_cents": alternative["upfront_cents"], "market_value": market_value_range(offers, changes),
            "draw_low_cents": draw_mark(lower, "low_cents"), "draw_high_cents": draw_mark(upper, "high_cents"),
            "outcomes": [{"from_strike": strike, "extra_payout_cents": payout, "min_draw_cents": low, "max_draw_cents": high}
                         for strike, payout, low, high in zip(strikes, payouts, lower, upper)]}


def pump_target(offers, gaps, pump_cash, reserve_cash, spend_limit, gallon_cents=0):
    capacity = reserve_cash + pump_cash
    floor = residual_floor(offers, gaps)
    plan = portfolio(offers, gaps, fuel_cash=max(capacity, floor))
    floor_option = None
    if floor > capacity and offers:
        floor_option = {"short_cents": floor - capacity, "premium_cents": plan["upfront_cents"],
                       "below_strike": offers[0].get("strike") if floor == gaps[0] else None}
    loan_free = plan if floor <= capacity else None
    minimum = loan_free["upfront_cents"] if loan_free else None
    if spend_limit is not None and plan["upfront_cents"] > spend_limit:
        best = portfolio(offers, gaps, spend_limit=spend_limit, minimize_residual=True)
        plan = portfolio(offers, gaps, fuel_cash=best["residual_cents"], spend_limit=spend_limit)
        if plan is None:
            raise ValueError("Could not reconcile the nearest attainable pump budget")

    risk = None
    if plan["legs"] and plan["residual_cents"] > capacity and all("strike" in o for o in offers):
        def weight(candidate):
            quantities = {o["strike"]: o["lots"] for o in candidate["legs"]}
            return funding_weight(offers, gaps, [quantities.get(o["strike"], 0) for o in offers], capacity)

        before = weight(plan)
        if before is not None:
            peak = plan["residual_cents"]
            terms = {"spend_limit": plan["upfront_cents"], "risk_capacity": capacity}
            candidate = portfolio(offers, gaps, fuel_cash=peak, **terms)
            if candidate:
                after = weight(candidate)
                risk = {"before_pct": before, "after_pct": after}
                if after < before:
                    plan = candidate
                if gallon_cents and after:
                    room = min(gaps[-1], plan["residual_cents"] + gallon_cents)
                    wider = portfolio(offers, gaps, fuel_cash=room, **terms)
                    if wider and weight(wider) < after:
                        best = portfolio(offers, gaps, fuel_cash=room, risk_limit=weight(wider), **terms)
                        if best:
                            risk["alternative"] = {"weight_pct": weight(best), "plan": best,
                                                   "short_cents": max(0, best["residual_cents"] - capacity)}

    def at_stop(residual):
        draw = min(reserve_cash, residual)
        outside = residual - draw
        return {"reserve_cents": draw, "outside_cents": outside, "short_cents": max(0, outside - pump_cash)}

    if risk and "alternative" in risk:
        alternative = risk["alternative"]
        best = alternative["plan"]
        alternative["after"] = at_stop(best["residual_cents"])
        alternative["protection_change"] = protection_change(offers, gaps, capacity, best, loan_free) if loan_free else None
    lots = max(0, gaps[-1] - capacity)
    single = min((sum(cost(lots, offer)) for offer, gap in zip(offers, gaps)
                  if gap <= capacity and offer["depth"] >= lots), default=None) if lots else 0
    change = None
    if loan_free and plan["residual_cents"] > capacity and all("strike" in o for o in offers):
        change = protection_change(offers, gaps, capacity, plan, loan_free)
    return {"floor_option": floor_option, "funding_risk": risk, "protection_change": change, "before": at_stop(gaps[-1]), "after": at_stop(plan["residual_cents"]), "single_spend_cents": single,
            "plan": plan, "minimum_spend_cents": minimum}


def pump_price_check(samples, offers, plan, trip, fill, as_of):
    prices = fill_replay.prices()["diesel_mills"][trip["region"]]
    result = pump_value.evaluate(samples, offers, plan, prices, fill, as_of)
    if result and len(plan["legs"]) == 1:
        leg, = plan["legs"]
        ticks = [(price, sum(cost(leg["lots"], {"price": price}))) for price in range(1, 100)]
        def highest(limit):
            return max((price for price, all_in in ticks if all_in <= limit), default=None)
        result["single_quote"] = {"ask": leg["price"], "strike": leg["strike"],
                                  "mean_limit": highest(result["mean_ceiling_cents"]),
                                  "tail_limit": highest(result["tail_ceiling_cents"])}
    return result


def compare_past_fills(target, offers, gaps, trip, anchor, fuel_cash, as_of, fuel):
    reference = datetime.fromisoformat(fuel["close_expiry"]).astimezone(timezone.utc).date()
    expiry = datetime.fromisoformat(fuel["expiry"]).astimezone(timezone.utc).date()
    offsets = fill_replay.expiry_offsets(reference, expiry) if offers else None
    if offers and offsets is None:
        return
    early, later = fill_replay.fills(trip, anchor, offers, fuel_cash, as_of, offsets)
    if not later:
        return
    base = target["plan"]
    cash = fill_replay.summary(later, offers, {"legs": [], "upfront_cents": 0})

    checks = {}
    fill_day = expiry + timedelta(days=offsets[1]) if offsets else as_of + timedelta(days=(-as_of.weekday()) % 7)

    def backup_check(plan, gaps, horizon=None):
        check = backup.estimate(later, gaps, as_of)
        if check:
            check["payday"] = {days: payday.estimate(later, trip, fuel_cash, offers, plan, as_of, days,
                                                     payout_stop=2 if horizon else 1)
                               for days in ((horizon,) if horizon else (23, 30))}
            check["volatility"] = fuel_volatility.compare(check, later, trip, offers, plan, anchor, fuel_cash, offsets, fill_day)
        return check

    cash_check = backup_check({"legs": []}, [row[1] for row in later])
    if cash_check:
        checks["cash_mix"] = cash_check
        target["backup_checks"] = checks

    def replay(plan, name, horizon=None):
        window = {"from": reference.isoformat(), "to": expiry.isoformat()} if offsets else None
        after = fill_replay.summary(later, offers, plan)
        after["one_late"] = fill_replay.one_late_summary(later, offers, plan)
        if plan["legs"]:
            ready, _ = fill_replay.pump_gaps(later, offers, plan)
            if offsets:
                after["pump_value"] = pump_price_check(later, offers, plan, trip, expiry + timedelta(days=offsets[1]), as_of)
            if cash_check:
                checks[name] = backup_check(plan, ready, horizon)
            after["delays"] = fill_replay.delay_threshold(ready, [row[1] for row in later], plan["upfront_cents"])
        return {"before": cash, "after": after, "oil_window": window}

    target["fill_replay"] = replay(base, "original")
    risk = target["funding_risk"]
    if risk and "alternative" in risk:
        other = risk["alternative"]
        other["fill_replay"] = replay(other["plan"], "risk")
    if not base["legs"]:
        return
    cash_plan = {"legs": [], "upfront_cents": 0, "cash_cents": gaps[-1], "residual_cents": gaps[-1]}
    capacity = fuel_cash + trip["pump_cash_cents"]
    loan_free = portfolio(offers, gaps, fuel_cash=capacity) if target["minimum_spend_cents"] is not None else None
    target["cash_mix"] = {"plan": cash_plan, "after": target["before"], "fill_replay": replay(cash_plan, "cash_mix"),
                          "protection_change": protection_change(offers, gaps, capacity, cash_plan, loan_free) if loan_free else None}
    if not early:
        return
    residual_cap = base["residual_cents"] + trip["pump_mills"] // 10

    def preview(candidate, name, horizon=None):
        residual = candidate["residual_cents"]
        draw = min(residual, fuel_cash)
        short = max(0, residual - capacity)
        return {"plan": candidate, "after": {"reserve_cents": draw, "outside_cents": residual-draw, "short_cents": short},
                "protection_change": protection_change(offers, gaps, capacity, candidate, loan_free) if short and loan_free else None,
                "fill_replay": replay(candidate, name, horizon)}

    for key, one_late in (("history_mix", False), ("delay_mix", True)):
        candidate = portfolio(offers, gaps, fuel_cash=residual_cap, spend_limit=base["upfront_cents"],
                              tail_samples=early, one_late=one_late)
        if candidate is None:
            continue
        evaluate = fill_replay.one_late_summary if one_late else fill_replay.summary
        original = evaluate(early, offers, base)["tail_cash_cents"]
        fitted = evaluate(early, offers, candidate)["tail_cash_cents"]
        if fitted >= original:
            continue
        choice = {**preview(candidate, key), "training_weeks": len(early), "short_training_weeks": sum(row[1] > 0 for row in early),
                  "original_train_cents": original, "train_cents": fitted}
        if not one_late:
            choice["sensitivity"] = fill_replay.month_sensitivity(later, offers, base, candidate)
        target[key] = choice

    cutoff = datetime.fromisoformat(regional_diesel.TEST_START).date() - timedelta(days=1)
    for days in (23, 30):
        stops = (days + 6) // 7
        train = payday.hedge_paths(early, trip, fuel_cash, offers, stops, cutoff)
        if len(train) < backup.WINDOW:
            continue
        candidate = portfolio(offers, gaps, fuel_cash=residual_cap, spend_limit=base["upfront_cents"],
                              tail_samples=train, first_fill_floor=True)
        if not candidate or not candidate["legs"]:
            continue
        future = payday.hedge_paths(later, trip, fuel_cash, offers, stops, as_of - backup.WEEK)
        report = payday.hedge_replay(train, future, stops, offers, {"cash": cash_plan, "original": base, "fitted": candidate})
        if not report or report["scores"]["fitted"]["training"]["late_cents"] >= report["scores"]["original"]["training"]["late_cents"]:
            continue
        name = f"payday_{days}"
        report["price_policy"] = payday_price.price_policy(train, future, report, stops, fill_day, as_of)
        limits = report["price_policy"]["current"]
        if limits:
            for mode in ("average", "hard"):
                rule = limits[mode]
                rule["quote_targets"] = {label: basket_quote(plan["legs"], rule["max_price_cents"][label])
                                         for label, plan in (("original", base), ("fitted", candidate))}
        if checks.get("original"):
            checks["original"].setdefault("late_payday", {})[days] = payday.estimate(later, trip, fuel_cash, offers, base, as_of, days, payout_stop=2)
        target[name] = {**preview(candidate, name, days), "horizon": report}


def analyze(snapshot, trip, *, reserve=None):
    validate(trip)
    market = snapshot["market"]
    fuel = fuel_update.compare(market)
    rows = contracts(market)
    ceiling = max(Fraction(c["strike"]["value"]) for c in rows)
    anchor = Fraction(fuel["close"])
    base = Fraction(trip["pump_mills"] * trip["gallons"], 10)
    per_gallon = Fraction(fuel["cents_per_oil_dollar"]) / int(fuel["gallons"])
    slope = per_gallon * trip["gallons"]
    regional = regional_diesel.history(trip["region"], per_gallon) if "region" in trip else None
    allowance = regional["cents_per_gallon"] * trip["gallons"] if regional else 0

    fitted = None
    if regional:
        fitted = diesel_model.lookup(trip["region"], datetime.fromisoformat(snapshot["checked_at"]).date())
    recent = trip.get("diesel_model") == "recent"
    if recent and fitted is None:
        raise diesel_model.CalibrationUnavailable("The weekly diesel model has no calibration for this market date. Use the standard estimate or open the recorded comparison.")

    def gap(oil):
        if recent:
            move = diesel_model.change(fitted["curve"], oil - anchor)
            return max(0, ceil(base + trip["gallons"] * move) - trip["budget_cents"])
        return max(0, ceil(base + allowance + (oil - anchor)*slope) - trip["budget_cents"])

    offers = top_offers(snapshot, fuel, rows)
    gaps = [gap(Fraction(o["strike"])) for o in offers] + [gap(ceiling)]
    limit = trip.get("spend_limit_cents")
    unrestricted = portfolio(offers, gaps)
    combined = (portfolio(offers, gaps, spend_limit=limit)
                if limit is not None and unrestricted["upfront_cents"] > limit else unrestricted)
    single = min((portfolio([o], [g, gaps[-1]], spend_limit=limit)["cash_cents"] for o, g in zip(offers, gaps)), default=gaps[-1])
    result = {"trip": trip, "checked_at": snapshot["checked_at"], "expiry": fuel["expiry"], "anchor": str(fuel["close"]),
            "ceiling": str(ceiling), "pump_bill_cents": ceil(base), "cash_only_cents": gaps[-1], "best_single_cents": single,
            "offers": len(offers), "contracts_checked": len(rows), "combined": combined}
    if regional:
        result["regional"] = {**regional, "allowance_cents": allowance}
        if fitted:
            result["regional"]["comparison"] = {
                "weeks": fitted["weeks"], "through": fitted["through"], "standard": fitted["fixed"], "recent": fitted["recent"],
                "gain": fitted["gain"],
                "standard_bill_cents": ceil(base + allowance + (ceiling - anchor)*slope),
                "recent_bill_cents": ceil(base + trip["gallons"] * diesel_model.change(fitted["curve"], ceiling - anchor))}
        if recent:
            result["regional"].update(cents_per_gallon=fitted["curve"]["base_cents"], training_weeks=diesel_model.WINDOW,
                                      allowance_cents=fitted["curve"]["base_cents"] * trip["gallons"],
                                      test_weeks=fitted["weeks"], allowance_misses=fitted["recent"]["misses"],
                                      worst_miss_cents=fitted["recent"]["worst_miss_cents"],
                                      worst_week=fitted["recent"]["worst_week"], four_fills=fitted["recent"]["four_fills"])
        four = result["regional"]["four_fills"]
        if four:
            tail = Fraction(four["tail_cents_per_weekly_gallon"]) * trip["gallons"]
            result["regional"]["four_fills"] = {**four, "tail_cents": ceil(tail)}
    benchmark, selected = unrestricted, combined
    fuel_cash = 0
    if reserve is not None:
        available, road = reserve["available_cents"], reserve["road_cents"]
        fuel_cash = max(0, available - road)
        affordable = portfolio(offers, gaps, fuel_cash=fuel_cash) if available >= road else None
        minimum_spend = affordable["upfront_cents"] if affordable else None
        if affordable:
            benchmark = affordable
        if limit is not None and minimum_spend is not None and minimum_spend > limit:
            affordable = None
        selected = affordable or combined
        result["reserve"] = {**reserve, "fuel_cash_cents": fuel_cash, "before_shortfall_cents": max(0, road + gaps[-1] - available),
                             "plan": affordable, "minimum_spend_cents": minimum_spend}
        if affordable is None and available >= road and combined["legs"]:
            fuel_due = combined["residual_cents"] - fuel_cash
            result["reserve"]["split"] = {
                "reserve_cents": fuel_cash, "outside_fuel_cents": fuel_due,
                "upfront_cents": combined["upfront_cents"], "outside_total_cents": combined["upfront_cents"] + fuel_due,
            }
    if limit is not None and benchmark["upfront_cents"] > limit and (reserve is None or available >= road):
        before = benchmark["upfront_cents"] + max(0, benchmark["residual_cents"] - fuel_cash)
        after = selected["upfront_cents"] + max(0, selected["residual_cents"] - fuel_cash)
        result["limit_comparison"] = {"unrestricted_upfront_cents": benchmark["upfront_cents"],
                                      "kept_upfront_cents": benchmark["upfront_cents"] - selected["upfront_cents"],
                                      "outside_change_cents": after - before}
    if "pump_cash_cents" in trip:
        target = pump_target(offers, gaps, trip["pump_cash_cents"], fuel_cash, limit, trip["pump_mills"] // 10)
        result["pump_target"] = target
        if regional:
            as_of = datetime.fromisoformat(snapshot["checked_at"]).date()
            compare_past_fills(target, offers, gaps, trip, anchor, fuel_cash, as_of, fuel)

    return result


@cache
def recorded():
    return json.loads(gzip.decompress(SNAPSHOT.read_bytes()))


@cache
def saved():
    return analyze(recorded(), EXAMPLE)
