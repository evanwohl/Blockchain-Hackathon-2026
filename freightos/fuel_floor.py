"""Find the least separate cash that covers fuel and preserves a chosen payday balance."""
import argparse
import base64
import gzip
import json
import struct
from functools import cache, lru_cache
from hashlib import sha256
from math import isqrt
from pathlib import Path

from solders.pubkey import Pubkey

from freightos import atomic_fuel, devnet, fuel_swap, payday_policy, reserve_credit, stop_backup
from hedging.quick_pay import integer

Q64 = 1 << 64
SAMPLE = stop_backup.SAMPLE.with_name("fuel_split_curve.json.gz")


def ceil_div(n, d):
    return -(-n // d)


@lru_cache(maxsize=32)
def upper_tick_price(tick):
    if abs(tick) > 443636:
        raise ValueError("Unsupported Whirlpool tick")
    # Orca rounds tick prices down. An upper bound keeps the quote strictly before the next tick.
    a, b = (10001, 10000) if tick >= 0 else (10000, 10001)
    return 1 + isqrt((Q64**2 * a**abs(tick)) // b**abs(tick))


def account_bytes(account, kind):
    if not account or account["owner"] != str(fuel_swap.ORCA) or account["executable"]:
        raise ValueError("The pricing account must belong to Orca")
    raw = base64.b64decode(account["data"][0], validate=True)
    if raw[:8] != sha256(("account:" + kind).encode()).digest()[:8]:
        raise ValueError("Unsupported Whirlpool account layout")
    return raw


def read_curves(slot, rpc):
    keys = []
    for pool in fuel_swap.POOLS:
        route, slot = fuel_swap.pool_accounts(pool, slot, rpc)
        keys.extend((str(pool), str(route[3])))
    snap = rpc("getMultipleAccounts", [keys, {"encoding": "base64", "commitment": "confirmed", "minContextSlot": slot}])
    if len(snap["value"]) != len(keys) or snap["context"]["slot"] < slot:
        raise ValueError("Pool curves predate the fuel quote")
    rows = []
    for i, pool in enumerate(fuel_swap.POOLS):
        state, ticks = snap["value"][2*i:2*i+2]
        raw, array = account_bytes(state, "Whirlpool"), account_bytes(ticks, "TickArray")
        if len(raw) < 245 or len(array) != 9988:
            raise ValueError("Unsupported pool or fixed tick array length")
        spacing, fee_seed, fee = struct.unpack_from("<HHH", raw, 41)
        tick = struct.unpack_from("<i", raw, 81)[0]
        start = struct.unpack_from("<i", array, 8)[0]
        expected, _ = Pubkey.find_program_address([b"tick_array", bytes(pool), str(start).encode()], fuel_swap.ORCA)
        if (not spacing or spacing != fee_seed or fee > 60000 or array[-32:] != bytes(pool)
                or raw[101:133] != bytes(fuel_swap.credit.SOL) or raw[181:213] != bytes(devnet.MINT)
                or start != tick // (88 * spacing) * (88 * spacing) or keys[2*i+1] != str(expected)):
            raise ValueError("Unsupported pool fee, token pair or current tick array")
        initialized = [start + j * spacing for j in range((tick - start) // spacing + 1) if array[12 + 113*j]]
        price = int.from_bytes(raw[65:81], "little")
        liquidity = int.from_bytes(raw[49:65], "little")
        if not price or not liquidity:
            raise ValueError("The pool has no active liquidity")
        bottom = upper_tick_price(max(initialized, default=start))
        rows.append({"pool": str(pool), "price": price, "liquidity": liquidity, "fee": fee,
                     "max_raw": max(0, (price-bottom)*liquidity // Q64 - 1)})
    return rows, snap["context"]["slot"]


def swap_cost(curve, output):
    if output > curve["max_raw"]:
        raise ValueError("Payment crosses the observed liquidity range")
    price, liquidity = curve["price"], curve["liquidity"]
    change = ceil_div(output * Q64, liquidity)
    numerator = liquidity * change * Q64
    if numerator.bit_length() > 256:
        raise ValueError("The required swap exceeds the Whirlpool calculation range")
    amount = ceil_div(numerator, price * (price - change))
    gross = ceil_div(amount * 1000000, 1000000 - curve["fee"])
    if gross.bit_length() > 64:
        raise ValueError("The SOL input exceeds the supported token amount")
    return gross


def price_payment(market, separate, received, high):
    vendor = market["bill_cents"] - separate
    wallet = min(vendor, market["wallet_cents"])
    reserve = min(vendor - wallet, market["reserve_cents"])
    output = vendor - wallet - reserve
    spent, pool = (0, None)
    if output:
        spent, pool = min((swap_cost(c, output * market["scale"]), c["pool"]) for c in market["curves"])
    restore = stop_backup.buffered(spent) + (market["fee_lamports"] if vendor else 0)
    if restore + (market["rent_lamports"] if output else 0) > market["cap_lamports"]:
        return None
    payday = received - market["bill_cents"] + output - ceil_div(restore * high, market["scale"] * 100000)
    return {"separate_cents": separate, "vendor_cents": vendor, "exchange_cents": output,
            "wallet_cash_cents": wallet, "reserve_cents": reserve, "restore_lamports": restore,
            "spent_lamports": spent, "pool": pool, "least_cents": payday}


def best_split(market, cash, received, required, high):
    maximum = max(0, market["bill_cents"] - market["wallet_cents"] - market["reserve_cents"]) * market["scale"]
    if any(c["max_raw"] < maximum for c in market["curves"]):
        raise ValueError("The bill extends beyond the verified liquidity range")
    strongest = None
    for separate in range(min(market["bill_cents"], cash) + 1):
        row = price_payment(market, separate, received, high)
        if row is None:
            continue
        if row["least_cents"] >= required:
            return row
        if strongest is None or row["least_cents"] > strongest["least_cents"]:
            strongest = row
    return strongest


def snapshot(plan, reserve, wallet, cap, rent, fee, slot, rpc, scenarios):
    curves, slot = read_curves(slot, rpc)
    scale = reserve["raw_units_per_cent"]
    market = {"scale": scale, "bill_cents": reserve["fuel_cents"], "wallet_cents": int(wallet["usdc_raw"]) // scale,
              "reserve_cents": reserve["vendor_raw"] // scale, "cap_lamports": cap, "fee_lamports": fee,
              "rent_lamports": rent, "curves": curves, "curve_slot": slot}
    checks = {}
    for cash, received, required, high in scenarios:
        pick = best_split(market, cash, received, required, high)
        if not pick:
            continue
        for target in (pick["vendor_cents"], min(reserve["fuel_cents"], pick["vendor_cents"] + 1)):
            if not target or str(target) in checks:
                continue
            rows, slot = stop_backup.fixed_payment(plan, reserve, wallet, rent, fee, cap, target, slot, rpc)
            for row in rows:
                if row["pool"]:
                    c = next(c for c in curves if c["pool"] == row["pool"])
                    if swap_cost(c, row["swap_raw"]) != row["spent_lamports"]:
                        raise ValueError("The pool changed between the curve and the vendor payment")
            if target == pick["vendor_cents"] and not any(r["pool"] == pick["pool"] and r["spent_lamports"] == pick["spent_lamports"]
                    and r["wallet_raw"] == pick["wallet_cash_cents"]*scale and r["reserve_raw"] == pick["reserve_cents"]*scale for r in rows):
                raise ValueError("The selected payment did not reproduce on Devnet")
            checks[str(target)] = rows
    return {**market, "checks": checks}, slot


def verified_curves(quote):
    if quote["unavailable_pools"]:
        raise ValueError("An endpoint pool declined the transfer")
    market = quote["market"]
    curves = {c["pool"]: c for c in market["curves"]}
    for row in quote["routes"] + quote["fixed_routes"]:
        if row["pool"] and swap_cost(curves[row["pool"]], row["swap_raw"]) != row["spent_lamports"]:
            raise ValueError("An endpoint payment differs from the pool curve")
    return market


def decisions(quote, receipts, required, prices):
    market = verified_curves(quote)
    options = {}
    available = quote["separate_cents"]
    for received in receipts:
        pick = best_split(market, available, received, required, max(prices))
        if pick is not None:
            checks = market["checks"].get(str(pick["vendor_cents"]), [])
            matches = [r for r in checks + quote["routes"] + quote["fixed_routes"]
                       if r["vendor_raw"] == pick["vendor_cents"] * quote["scale"] and r["pool"] == pick["pool"]
                       and r["spent_lamports"] == pick["spent_lamports"] and r["limit_lamports"] + r["fee_lamports"] == pick["restore_lamports"]]
            best = matches[0] if matches else None
            if pick["least_cents"] >= required and pick["separate_cents"]:
                previous = price_payment(market, pick["separate_cents"] - 1, received, max(prices))
                pick["one_cent_less"] = {"least_cents": previous["least_cents"] if previous else None}
            principal = received - market["bill_cents"] + pick["exchange_cents"]
            pick.update(untouched_cents=available - pick["separate_cents"],
                        most_cents=principal - ceil_div(pick["restore_lamports"] * min(prices), market["scale"] * 100000),
                        payment_slot=best["slot"] if best else None,
                        protected_cents=best["retained_raw"] // market["scale"] if best else quote["protected_cents"])
        options[str(received)] = pick
    return {"by_receipt": options, "slot": market["curve_slot"], "pool_count": len(market["curves"])}


def public_quotes(cap, cash, received, floor, high, rpc):
    def inspect(plan, reserve, wallet, cap, rent, fee, slot, rpc):
        try:
            result, end = snapshot(plan, reserve, wallet, cap, rent, fee, slot, rpc, [(cash, received, floor, high)])
            if end - min(reserve["slot"], wallet["slot"]) <= 240:
                return result, end
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
            pass
        return None, slot
    return stop_backup.observe(cap, rpc, outside_cents=cash, inspect_market=inspect)


def replay(data):
    def inspect(*args):
        return snapshot(*args, scenarios=data["scenarios"])
    q = atomic_fuel.replay_calls(data["calls"], lambda rpc: stop_backup.observe(
        stop_backup.DEFAULT_CAP, rpc, outside_cents=7500, inspect_market=inspect))
    if q != data["result"]:
        raise ValueError("The recorded cash floor differs from its public simulations")
    verified_curves(q)
    return q


@cache
def saved():
    raw = gzip.decompress(SAMPLE.read_bytes())
    if sha256(raw).hexdigest() != "068b8d83186c1ab5b57da1e751914e5a4e5195aa66ab0dba61c2b2f4577b6a8b":
        raise ValueError("The pool curves or fuel payment proof changed")
    return json.loads(raw)["result"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--gemini-evidence", type=Path)
    parser.add_argument("--stress-cents", type=int, default=15000)
    parser.add_argument("--floor-cents", type=int, default=300)
    parser.add_argument("--received-cents", type=int, default=20000)
    args = parser.parse_args()
    integer(args.stress_cents, "stress price", 10000000)
    integer(args.floor_cents, "payday target", 100000000)
    integer(args.received_cents, "freight payment", reserve_credit.INVOICE_CENTS)
    if args.gemini_evidence:
        payday_policy.verify_history(args.gemini_evidence.read_bytes())
    q = replay(json.loads(args.evidence.read_bytes() if args.evidence else gzip.decompress(SAMPLE.read_bytes())))
    ref = payday_policy.reference_price(payday_policy.recorded_menu()[0])
    cases, _ = payday_policy.price_cases(ref, args.stress_cents)
    print(json.dumps(decisions(q, [args.received_cents], args.floor_cents, [c["price_cents"] for c in cases]), indent=2))
