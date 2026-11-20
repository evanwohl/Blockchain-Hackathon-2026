"""Size a debt-free emergency fuel payment using public exact-output swap simulations."""
import argparse
import gzip
import json
from functools import cache
from hashlib import sha256

from freightos import atomic_fuel, devnet, fuel_swap, next_stop, reserve_credit
from hedging.quick_pay import integer

SAMPLE = fuel_swap.SAMPLE.with_name("stop_backup.json.gz")
# $80 of SOL at the funding planner’s recorded $72.82 reference and 1:100 demo scale.
DEFAULT_CAP = 8000 * 10_000_000 // 7282


def buffered(spent):
    return (spent * 10050 + 9999) // 10000


def boundary(full, allowance, measure):
    low, cheap = 0, 0
    high = full
    expensive = measure(high)
    if expensive <= allowance:
        return high
    rounds = 0
    while high - low > 1:
        rounds += 1
        guess = low + (allowance - cheap) * (high - low) // (expensive - cheap)
        trial = (high + low) // 2 if rounds > 3 else min(high - 1, max(low + 1, guess))
        spent = measure(trial)
        if not cheap <= spent <= expensive:
            raise ValueError("The pool changed while sizing the backup")
        if spent <= allowance:
            low, cheap = trial, spent
        else:
            high, expensive = trial, spent
    return low


def pool_backup(plan, pool, reserve, wallet, base, rent, fee, cap, first_slot, slot, rpc):
    route, slot = fuel_swap.pool_accounts(pool, slot, rpc)
    full = reserve["fuel_cents"] - base
    costs = {}

    def quote(cents, maximum, follow=False):
        nonlocal slot
        q = fuel_swap.simulate(plan, route, cents * reserve["raw_units_per_cent"], maximum, rent,
                               reserve, full - cents, wallet, slot, rpc, follow=follow)
        slot = q["slot"]
        if q["fee_lamports"] != fee or slot - first_slot > 240:
            raise ValueError("The backup fee or market window changed")
        return q

    def measure(cents):
        q = quote(cents, wallet["lamports"] - rent - fee)
        costs[cents] = q["spent_lamports"]
        return buffered(q["spent_lamports"])

    amount = boundary(full, cap - rent - fee, measure)
    if not amount:
        return None, slot
    maximum = buffered(costs[amount])
    sized = quote(amount, maximum)
    follow = quote(amount, maximum, True)
    if any(q["spent_lamports"] != costs[amount] for q in (sized, follow)):
        raise ValueError("Pool price changed during the bounded payment check")
    next_entry = rent + fee + buffered(costs[amount + 1]) if amount < full else None
    return {**sized, "retained_raw": follow["retained_raw"], "next_cent_entry_lamports": next_entry}, slot


def observe(cap, rpc):
    integer(cap, "SOL available for the backup", 10**12)
    if rpc("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("The backup requires public Devnet")
    reserve = reserve_credit.reserve_leg(rpc)
    plan = next_stop.payment()
    wallet = devnet.wallet_balances(str(plan.payer), rpc)
    cap = min(cap, wallet["lamports"])
    scale, bill = reserve["raw_units_per_cent"], reserve["fuel_cents"]
    cash = min(int(wallet["usdc_raw"]) // scale, bill)
    reserve = fuel_swap.spendable_reserve(reserve, 0, int(wallet["usdc_raw"]), rpc)
    vault = reserve["vendor_raw"] // scale
    base = cash + vault
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    slot = max(wallet["slot"], reserve["slot"])
    first_slot = min(wallet["slot"], reserve["slot"])
    baseline = fuel_swap.simulate(plan, None, 0, 0, rent, reserve, bill - base, wallet, slot, rpc)
    protected = fuel_swap.simulate(plan, None, 0, 0, rent, reserve, bill - base, wallet, baseline["slot"], rpc, follow=True)
    baseline["retained_raw"] = protected["retained_raw"]
    fee, slot = baseline["fee_lamports"], protected["slot"]
    rows, unavailable = [], []
    if cap >= fee:
        rows.append(baseline)
    if base < bill and cap > rent + fee:
        for pool in fuel_swap.POOLS:
            try:
                row, slot = pool_backup(plan, pool, reserve, wallet, base, rent, fee, cap, first_slot, slot, rpc)
            except fuel_swap.PoolDeclined as declined:
                slot = declined.slot
                unavailable.append(str(pool))
                continue
            if row is not None:
                rows.append(row)
    if slot - first_slot > 240:
        raise ValueError("The backup quotes are too far apart")
    best = min(rows, key=lambda q: (-q["vendor_raw"], q["spent_lamports"], q["entry_lamports"]), default=None)
    paid = best["vendor_raw"] // scale if best else 0
    return {"bill_cents": bill, "covered_cents": paid, "outside_cents": bill - paid, "cap_lamports": cap,
            "best": best, "routes": rows, "unavailable_pools": unavailable, "first_slot": first_slot, "last_slot": slot, "scale": scale,
            "protected_cents": reserve["holdback"]["cost_cents"]}


def replay(data):
    observed = atomic_fuel.replay_calls(data["calls"], lambda rpc: observe(data["cap_lamports"], rpc))
    if "result" in data and observed != data["result"]:
        raise ValueError("The saved backup differs from the simulated payments")
    return observed


@cache
def saved():
    raw = gzip.decompress(SAMPLE.read_bytes())
    if sha256(raw).hexdigest() != "a028df07e14fa73d0873ab175166e05283c9ea127503f1f04b35fbe1858f29e1":
        raise ValueError("The recorded backup evidence changed")
    return json.loads(raw)["result"]


def payday(quote, prices, floor):
    best = quote["best"]
    spent = (best["limit_lamports"] + best["fee_lamports"]) if best else 0
    paid_from_cash = (best["wallet_raw"] + best["reserve_raw"]) // quote["scale"] if best else 0
    principal = reserve_credit.INVOICE_CENTS - quote["outside_cents"] - paid_from_cash
    denominator = 100 * quote["scale"] * 10**9
    def remaining(price):
        return principal - (spent * price * 10**6 + denominator - 1) // denominator
    least, most = remaining(max(prices)), remaining(min(prices))
    return {"least_cents": least, "most_cents": most, "target_gap_cents": max(0, floor - least)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--cap-lamports", type=int, default=DEFAULT_CAP)
    parser.add_argument("--evidence", type=argparse.FileType("r"))
    args = parser.parse_args()
    if args.evidence:
        result = replay(json.load(args.evidence))
    elif args.live:
        result = observe(args.cap_lamports, devnet.read_with_retry())
    else:
        if args.cap_lamports != DEFAULT_CAP:
            parser.error("Use --live for another SOL limit")
        result = replay(json.loads(gzip.decompress(SAMPLE.read_bytes())))
    print(json.dumps(result, indent=2))
