"""Reprice a full-size fuel stop from finalized Orca pool accounts."""
import hashlib
import json
import struct
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import numpy as np
from solders.pubkey import Pubkey

from freightos import fuel_floor, fuel_swap, reserve, stop_backup

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


def compare(plan, market, native, budget, cash, evidence=None):
    data, pools = saved() if evidence is None else verify(evidence)
    bill, reference = plan["bill_cents"], plan["reference_cents"]
    costs, second_legs = sale_table(bill) if evidence is None else split_costs(pools, bill)
    scale_factor = SCALE // market["scale"]
    wallet_cap = min(native * scale_factor, budget * LAMPORTS // reference)
    stable = market["wallet_cents"] + market["reserve_cents"]
    high, received, target = plan["high_price_cents"], plan["receipt_cents"], plan["floor_cents"]
    fee = market["fee_lamports"]
    rent = data["rent"]

    def price(separate):
        vendor = bill - separate
        sale = max(0, vendor - stable)
        if sale >= len(costs):
            return None
        restore = costs[sale] + (fee if vendor else 0)
        opening = restore + (rent if sale else 0)
        payday = received - bill + sale - fuel_floor.ceil_div(restore * high, LAMPORTS)
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
    return {"slot": data["accounts"]["context"]["slot"], "date": datetime.fromtimestamp(data["block_time"], timezone.utc).isoformat(),
            "covers_full_range": covered_range, "original": comparable, "first_cash_cents": first,
            "fuel_short_cents": max(0, first - cash), "target_cash_cents": preserving,
            "target_sale_cents": target_sale, "target_second_pool_cents": second_legs[target_sale] if target_sale is not None else None,
            "target_plan_payday_cents": price(preserving)[1] if preserving is not None else None,
            "payday_gap_cents": max(0, target - best_payday) if best_payday is not None and not funded else None,
            "spot_cents": [(p["price"]**2 * 100000 + fuel_floor.Q64**2 // 2) // fuel_floor.Q64**2 for p in pools],
            "pool_addresses": [p["pool"] for p in pools], "network_fee_lamports": fee, "rent_lamports": rent}
