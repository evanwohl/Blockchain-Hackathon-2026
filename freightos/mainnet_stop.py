"""Reprice a full-size fuel stop from finalized Orca pool accounts."""
import hashlib
import json
import struct
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import numpy as np
from solders.pubkey import Pubkey
from hedging.quick_pay import integer

from freightos import customer_credit, fuel_floor, fuel_swap, payday_policy, reserve, reserve_credit, stop_backup

EVIDENCE = Path(__file__).with_name("artifacts") / "mainnet_fuel" / "pools.json"
DIGEST = "3de18210abdfd86e2a7e72cf91284099c623dadd28daf38f211c5466052c7134"
CONFIG = Pubkey.from_string("2LecshUwdy9xi7meFgHtFJQNSKk4KdTrcpvaB56dP2NQ")
SCALE = 10000
LAMPORTS = 10**9


def curves(data):
    if data["genesis"] != "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d":
        raise ValueError("The pool prices must come from Solana mainnet")
    result = []
    for key, account, spacing in zip(data["addresses"], data["accounts"]["value"], (4, 64), strict=True):
        seeds = [b"whirlpool", bytes(CONFIG), bytes(fuel_swap.credit.SOL), bytes(reserve.USDC), spacing.to_bytes(2, "little")]
        pool, _ = Pubkey.find_program_address(seeds, fuel_swap.ORCA)
        raw = fuel_floor.account_bytes(account, "Whirlpool")
        if (str(pool) != key or len(raw) != 653 or raw[8:40] != bytes(CONFIG) or
                raw[101:133] != seeds[2] or raw[181:213] != seeds[3]):
            raise ValueError("The mainnet pool or token pair changed")
        step, fee_tier, fee = struct.unpack_from("<HHH", raw, 41)
        tick = struct.unpack_from("<i", raw, 81)[0]
        price = int.from_bytes(raw[65:81], "little")
        liquidity = int.from_bytes(raw[49:65], "little")
        if step != spacing or fee_tier != spacing or fee >= 1000000 or not liquidity or not price:
            raise ValueError("Unsupported mainnet liquidity or fee tier")
        # Stop before the nearest possible initialized tick. The quote needs no assumption about the next range.
        bottom = fuel_floor.upper_tick_price(tick // spacing * spacing)
        capacity = max(0, (price - bottom) * liquidity // fuel_floor.Q64 - 1)
        result.append(dict(pool=key, price=price, liquidity=liquidity, fee=fee, max_raw=capacity))
    return result


def split_costs(pools, total):
    caps = [[stop_backup.buffered(fuel_floor.swap_cost(pool, cents * SCALE))
             for cents in range(min(total, pool["max_raw"] // SCALE) + 1)] for pool in pools]
    first, second = caps
    limit = min(total, len(first) + len(second) - 2)
    dtype = np.int64 if first[-1] + second[-1] <= np.iinfo(np.int64).max else object
    a, b = np.asarray(first, dtype=dtype), np.asarray(second, dtype=dtype)
    costs, second_legs = [], []
    for volume in range(limit + 1):
        low, high = max(0, volume - len(first) + 1), min(volume, len(second) - 1)
        values = a[volume-high:volume-low+1][::-1] + b[low:high+1]
        index = int(values.argmin())
        cost = int(values[index])
        leg = low + index
        if volume < len(first) and first[volume] == cost:
            leg = 0
        elif volume < len(second) and second[volume] == cost:
            leg = volume
        costs.append(cost)
        second_legs.append(leg)
    return costs, second_legs


def verify(raw):
    if hashlib.sha256(raw).hexdigest() != DIGEST:
        raise ValueError("The mainnet pool evidence changed")
    data = json.loads(raw)
    return data, curves(data)


@cache
def saved():
    return verify(EVIDENCE.read_bytes())


@cache
def sale_table(bill):
    return split_costs(saved()[1], bill)


def validate_offer(menu, fee, amount=None):
    if menu == "counter":
        if not integer(amount, "net amount the customer offers", reserve_credit.INVOICE_CENTS):
            raise ValueError("Enter a positive early payment")
        integer(fee, "flat early-payment discount", reserve_credit.INVOICE_CENTS)
        return
    if menu not in ("partial", "both", "none"):
        raise ValueError("Choose the amounts the customer offers")
    integer(fee, "fee per $99 of early freight cash", 10000)


def early_freight(bill, stable, received, target, cash, wallet_cap, price, menu, fee):
    face = customer_credit.ADVANCES[-1]
    invoice_limit = received * face // (face + fee)
    smallest = most_cash = minimum_cash = max_fee = fuel_only = None

    def describe(key, cash_first=False):
        if key is None:
            return None
        own, advance = key if cash_first else (key[1], key[0])
        charge = customer_credit.customer_fee(advance, fee)
        vendor = bill - own - advance
        return {"advance_cents": advance, "cash_cents": own, "fee_cents": charge,
                "wallet_cents": vendor, "sale_cents": max(0, vendor - stable),
                "later_cents": received - advance - charge,
                "invoice_balance_cents": reserve_credit.INVOICE_CENTS - advance - charge,
                "payday_cents": price(own + advance)[1] - charge}

    for outside in range(bill + 1):
        quote = price(outside)
        if quote is None or quote[0] > wallet_cap:
            continue
        room = quote[1] - target
        needed = max(0, outside - cash)
        capacity = min(face, outside, invoice_limit)
        lots = customer_credit.MENUS.get(menu)
        first = needed if lots is None else next((lot for lot in lots if lot >= needed), face + 1)
        if first <= capacity:
            fuel_key = (first, customer_credit.customer_fee(first, fee) - quote[1], outside - first)
            if fuel_only is None or fuel_key < fuel_only:
                fuel_only = fuel_key
        if room < 0:
            continue
        maximum = min(capacity, room * face // fee) if fee else capacity
        last = maximum if lots is None else next(lot for lot in reversed(lots) if lot <= maximum)
        own = outside - last
        minimum_cash = own if minimum_cash is None else min(minimum_cash, own)
        if 0 < first <= min(outside, received, face):
            ceiling = min(room, received - first) * face // first
            max_fee = ceiling if max_fee is None else max(max_fee, ceiling)
        if first > last:
            continue
        request_key = (first, outside - first)
        cash_key = (own, last)
        if smallest is None or request_key < smallest:
            smallest = request_key
        if most_cash is None or cash_key < most_cash:
            most_cash = cash_key
    return {"menu": menu, "fee_per_99_cents": fee, "smallest": describe(smallest),
            "fuel_only": describe((fuel_only[0], fuel_only[2])) if fuel_only is not None else None,
            "keep_cash": describe(most_cash, cash_first=True), "minimum_cash_cents": minimum_cash,
            "max_fee_per_99_cents": max_fee if smallest is None or smallest[0] else None}


def counteroffer(bill, stable, received, target, cash, wallet_cap, price, net, fee):
    first_outside = best_payday = ceiling = nearest = selected = None
    used = min(net, bill)
    for outside in range(bill + 1):
        quote = price(outside)
        if quote is None or quote[0] > wallet_cap:
            continue
        if first_outside is None:
            first_outside = outside
        room = quote[1] - target
        own = outside - used
        if 0 <= own <= cash:
            allowance = min(room, received - net)
            if allowance >= 0:
                ceiling = allowance if ceiling is None else max(ceiling, allowance)
            payday = quote[1] - fee
            best_payday = payday if best_payday is None else max(best_payday, payday)
            if selected is None and allowance >= fee:
                wallet = bill - outside
                selected = {"advance_cents": net, "cash_cents": own, "fee_cents": fee,
                            "wallet_cents": wallet, "sale_cents": max(0, wallet - stable),
                            "later_cents": received - net - fee, "payday_cents": payday,
                            "invoice_balance_cents": reserve_credit.INVOICE_CENTS - net - fee}
        lower = max(1, outside - cash)
        upper = received - fee if outside == bill else min(outside, received - fee)
        if room >= fee and lower <= upper:
            amount = min(max(net, lower), upper)
            candidate = (abs(amount - net), amount)
            if nearest is None or candidate < nearest:
                nearest = candidate
    return {"net_cents": net, "flat_fee_cents": fee, "plan": selected, "surplus_cents": net - used,
            "receipt_gap_cents": max(0, net + fee - received),
            "fuel_gap_cents": max(0, first_outside - cash - used),
            "payday_gap_cents": None if best_payday is None else max(0, target - best_payday),
            "fee_ceiling_cents": ceiling, "nearest_net_cents": nearest[1] if nearest else None}


def price_crossover(wait_restore, early_restore, cash_cost, denominator=LAMPORTS):
    slope = wait_restore - early_restore
    if slope == 0:
        return {"constant_difference_cents": -cash_cost}

    def crossing(required, permanent):
        if required == 0:
            return 0
        low = (required - 1) * denominator // slope + 1
        high = fuel_floor.ceil_div(required * denominator, slope)
        start, end = low, high
        while start < end:
            mid = (start + end) // 2
            if permanent:
                gap = -payday_policy.worst_rounding_gap(early_restore, wait_restore, denominator, mid, high)[0]
            else:
                gap = payday_policy.worst_rounding_gap(wait_restore, early_restore, denominator, low, mid)[0]
            if gap >= required:
                end = mid
            else:
                start = mid + 1
        return start

    first = crossing(cash_cost, False)
    always = crossing(cash_cost + 1, True)
    tie_only = False
    if first < always:
        minimum = -payday_policy.worst_rounding_gap(early_restore, wait_restore, denominator, first, always-1)[0]
        maximum = payday_policy.worst_rounding_gap(wait_restore, early_restore, denominator, first, always-1)[0]
        tie_only = minimum == maximum == cash_cost
    return {"first_match_cents": first, "always_ahead_cents": always, "ties_only": tie_only}


def compare(plan, market, native, budget, cash, evidence=None, *, offer_menu="partial", offer_fee=100, offer_amount=None, history_prices=()):
    data, pools = saved() if evidence is None else verify(evidence)
    bill, reference = plan["bill_cents"], plan["reference_cents"]
    costs, second_legs = sale_table(bill) if evidence is None else split_costs(pools, bill)
    scale_factor = SCALE // market["scale"]
    wallet_cap = min(native * scale_factor, budget * LAMPORTS // reference)
    stable = market["wallet_cents"] + market["reserve_cents"]
    high, received, target = plan["high_price_cents"], plan["receipt_cents"], plan["floor_cents"]
    fee = market["fee_lamports"]
    rent = data["rent"]

    def price(separate, buyback=high):
        vendor = bill - separate
        sale = max(0, vendor - stable)
        if sale >= len(costs):
            return None
        restore = costs[sale] + (fee if vendor else 0)
        opening = restore + (rent if sale else 0)
        payday = received - bill + sale - fuel_floor.ceil_div(restore * buyback, LAMPORTS)
        return opening, payday

    first = preserving = best_payday = None
    for separate in range(bill + 1):
        quote = price(separate)
        if quote is None or quote[0] > wallet_cap:
            continue
        if first is None:
            first = separate
        if separate <= cash:
            best_payday = quote[1] if best_payday is None else max(best_payday, quote[1])
        if quote[1] >= target:
            preserving = separate
            break
    funded = preserving is not None and preserving <= cash
    original = plan["separate_cents"]
    quoted = price(original)
    sale = max(0, bill - original - stable)
    comparable = None
    if quoted:
        other = second_legs[sale]
        comparable = {"sale_cents": sale, "second_pool_cents": other, "opening_lamports": quoted[0],
                      "wallet_limit_cents": fuel_floor.ceil_div(quoted[0] * reference, LAMPORTS),
                      "payday_cents": quoted[1], "wallet_fits": quoted[0] <= wallet_cap,
                      "cash_fits": original <= cash}
    target_sale = None if preserving is None else max(0, bill - preserving - stable)
    covered_range = len(costs) > max(0, bill - stable)
    if offer_menu == "counter":
        counter = counteroffer(bill, stable, received, target, cash, wallet_cap, price, offer_amount, offer_fee)
        row = counter["plan"]
        counter["wait"] = None
        if row:
            own = row["cash_cents"]
            waiting = price(own)
            if waiting is not None:
                wait = {"budget_cents": fuel_floor.ceil_div(waiting[0] * reference, LAMPORTS),
                        "fits": waiting[0] <= wallet_cap}
                if not wait["fits"] and waiting[0] <= native * scale_factor:
                    wait["preview_budget_cents"] = wait["budget_cents"]
                if wait["fits"]:
                    wait["stress_difference_cents"] = row["payday_cents"] - waiting[1]
                    accepted_outside = bill - row["wallet_cents"]
                    wait_sale = max(0, bill - own - stable)
                    early_sale = row["sale_cents"]
                    wait_restore = costs[wait_sale] + (fee if own < bill else 0)
                    early_restore = costs[early_sale] + (fee if accepted_outside < bill else 0)
                    wait["crossover"] = price_crossover(wait_restore, early_restore, wait_sale - early_sale + offer_fee)

                    def outcome(level):
                        early_cash = price(accepted_outside, level)[1] - offer_fee
                        waiting_cash = price(own, level)[1]
                        maximum_fee = min(received - offer_amount, early_cash + offer_fee - max(target, waiting_cash))
                        return {"price_cents": level, "early_cents": early_cash, "wait_cents": waiting_cash,
                                "fee_limit_cents": maximum_fee if maximum_fee >= 0 else None}

                    wait["starting_price"] = outcome(reference)
                    wait["high_price"] = outcome(high)
                    if history_prices:
                        history = [outcome(level) for level in history_prices]
                        differences = [(case["early_cents"] - case["wait_cents"], case["price_cents"]) for case in history]
                        wait.update(windows=len(differences), lowest=min(differences), highest=max(differences),
                                    historical_low=outcome(min(history_prices)), historical_high=outcome(max(history_prices)))
                counter["wait"] = wait
        offer = {"menu": "counter", "counter": counter}
    else:
        offer = early_freight(bill, stable, received, target, cash, wallet_cap, price, offer_menu, offer_fee)
    return {"early_freight": offer, "slot": data["accounts"]["context"]["slot"], "date": datetime.fromtimestamp(data["block_time"], timezone.utc).isoformat(),
            "covers_full_range": covered_range, "original": comparable, "first_cash_cents": first,
            "fuel_short_cents": max(0, first - cash), "target_cash_cents": preserving,
            "target_sale_cents": target_sale, "target_second_pool_cents": second_legs[target_sale] if target_sale is not None else None,
            "target_plan_payday_cents": price(preserving)[1] if preserving is not None else None,
            "payday_gap_cents": max(0, target - best_payday) if best_payday is not None and not funded else None,
            "spot_cents": [(p["price"]**2 * 100000 + fuel_floor.Q64**2 // 2) // fuel_floor.Q64**2 for p in pools],
            "pool_addresses": [p["pool"] for p in pools], "network_fee_lamports": fee, "rent_lamports": rent}
