"""Price the remaining fuel payment when a customer offers a fixed invoice advance."""
import argparse
import gzip
import json
from decimal import Decimal
from functools import cache
from pathlib import Path

from freightos import atomic_fuel, devnet, freight_credit as credit, next_stop, reserve_credit
from hedging.quick_pay import integer

SAMPLE = credit.DATA / "customer_credit.json.gz"
ADVANCES = (0, 4950, 9900)
MENUS = {"none": (0,), "half": (0, 4950), "full": (0, 9900), "both": ADVANCES}
DAYS = 23
DROP = 40


def residual(reserve, net):
    remaining = reserve["fuel_cents"] - net
    shortfall = remaining * reserve["raw_units_per_cent"] - reserve["vendor_raw"]
    if reserve["fuel_cents"] != reserve_credit.BILL_CENTS or shortfall <= 0:
        raise ValueError("The customer payment and spare reserve no longer leave a loan to compare")
    return {**reserve, "fuel_cents": remaining, "shortfall_raw": shortfall}


def entry_budget(quote):
    return sum(quote[key] for key in ("pledge_lamports", "setup_lamports", "fee_lamports", "temporary_lamports"))


def check_entry(reserve, q, rpc):
    plan = next_stop.payment()
    config = {"commitment": "confirmed", "minContextSlot": q["remainder_slot"]}
    balance = rpc("getBalance", [str(plan.payer), config])
    slot, initial = balance["context"]["slot"], balance["value"]
    if slot < q["remainder_slot"]:
        raise ValueError("Opening balance predates the loan")
    rent = rpc("getMinimumBalanceForRentExemption", [165])
    block = rpc("getLatestBlockhash", [{**config, "minContextSlot": slot}])["value"]["blockhash"]
    limit = entry_budget(q)
    if initial <= limit:
        raise ValueError("The demo payer cannot test both opening boundaries")
    for cap in (limit, limit - 1):
        parked = initial - cap
        msg, _, _, result, _ = atomic_fuel.simulate(reserve, q["pledge_lamports"], rent, block, slot, rpc, parked=parked)
        slot, value = result["context"]["slot"], result["value"]
        keys = list(msg.account_keys)
        owner, destination = keys.index(plan.payer), keys.index(plan.payee)
        if value["preBalances"][owner] != initial or value["fee"] != q["fee_lamports"]:
            raise ValueError("The opening wallet or transaction fee changed between checks")
        if cap == limit:
            if value["err"] is not None:
                raise ValueError("The quoted wallet limit cannot open this loan")
            before, after = devnet.token_balances(value, keys, plan.destination, devnet.MINT, plan.payee)
            if (after - before != q["vendor_raw"] or value["postBalances"][owner] != q["temporary_lamports"]
                    or value["postBalances"][destination] - value["preBalances"][destination] != parked):
                raise ValueError("The capped loan did not pay the vendor and release its temporary deposits")
        else:
            expected = value["preBalances"].copy()
            expected[owner] -= value["fee"]
            need = q["obligation_rent_lamports"]
            if (not value["err"] or f"Transfer: insufficient lamports {need-1}, need {need}" not in value["logs"]
                    or value["postBalances"] != expected or value["postTokenBalances"] != value["preTokenBalances"]):
                raise ValueError("The one-lamport shortfall did not roll back as expected")
    return slot


def capture(rpc=devnet.request):
    data = {"reserve": [], "loans": {}, "entries": {}}
    reserve = reserve_credit.reserve_leg(atomic_fuel.record(data["reserve"], rpc))
    for net in ADVANCES:
        calls = data["loans"][str(net)] = []
        payment = residual(reserve, net)
        q = atomic_fuel.terms(payment, DROP, DAYS, atomic_fuel.record(calls, rpc), protect=True)
        boundary = data["entries"][str(net)] = []
        check_entry(payment, q, atomic_fuel.record(boundary, rpc))
    return data


def replay(data):
    reserve = reserve_credit.replay_reserve(data["reserve"])
    quotes = [atomic_fuel.replay(residual(reserve, net), DROP, DAYS, data["loans"][str(net)], protect=True)
              for net in ADVANCES]
    previous = reserve["slot"]
    for quote in quotes:
        if not previous <= quote["slot"] <= quote["refused_slot"] <= quote["remainder_slot"] <= reserve["slot"] + 150:
            raise ValueError("Loan observations fall outside the same reserve comparison window")
        previous = quote["remainder_slot"]
    if "entries" in data:
        for net, q in zip(ADVANCES, quotes, strict=True):
            q["entry_slot"] = atomic_fuel.replay_calls(data["entries"][str(net)], lambda rpc: check_entry(residual(reserve, net), q, rpc))
    return quotes


@cache
def saved():
    return replay(json.loads(gzip.decompress(SAMPLE.read_bytes())))


def inputs(menu, fee, funds, extra):
    if not isinstance(menu, str) or menu not in MENUS:
        raise ValueError("Choose the payments your customer offers")
    integer(fee, "fee on $99 of customer cash", 10000)
    integer(funds, "available test SOL in lamports", 10**12)
    integer(extra, "other loan expenses due by payday", 100000000)


def customer_fee(net, full_fee):
    return (net * full_fee + ADVANCES[-1] - 1) // ADVANCES[-1]


def next_fee_change(options, fee, funds, winner):
    if winner in (None, 0):
        return None
    eligible = [(r["advance_cents"], r["cost_cents"] - r["fee_cents"], r["payday_cash_cents"] + r["fee_cents"],
                 r["entry_lamports"]) for r in options if r["entry_lamports"] <= funds]
    for offer_fee in range(fee + 1, 10001):
        best = None
        for net, base_cost, cash_room, entry in eligible:
            charge = customer_fee(net, offer_fee)
            if charge <= cash_room:
                candidate = (base_cost + charge, entry, net)
                if best is None or candidate < best:
                    best = candidate
        chosen = best[2] if best else None
        if chosen != winner:
            return {"fee_cents": offer_fee, "advance_cents": chosen}
    return None


def compare(quotes, menu, fee, funds, extra):
    inputs(menu, fee, funds, extra)
    options = []
    baseline_rate = Decimal(quotes[0]["annual_rate"])
    for net, q in zip(ADVANCES, quotes, strict=True):
        if net not in MENUS[menu]:
            continue
        scale = q["scale"]
        borrowed, reserve = q["borrow_raw"] // scale, q["reserve_raw"] // scale
        if borrowed + reserve + net != reserve_credit.BILL_CENTS:
            raise ValueError("The three sources must cover the fuel bill exactly")
        discount = customer_fee(net, fee)
        regular = reserve_credit.INVOICE_CENTS - net - discount
        stress = q["payday_stress"]
        cash = regular - stress["repay_cents"] - reserve - extra
        entry = entry_budget(q)
        opening_fee = credit.ceiling(Decimal(q["sol_price"]) * q["fee_lamports"] * 10**6 / (scale * credit.LAMPORTS))
        cash_cost = discount + extra + opening_fee
        common_payoff = Decimal(q["initial_debt_raw"]) * (2 * baseline_rate * stress["days"] / 365).exp()
        missed = stress["cost_cents"] - (stress["repay_cents"] - borrowed)
        common_cost = credit.ceiling((common_payoff - q["borrow_raw"]) / scale) + missed + cash_cost
        options.append({"advance_cents": net, "fee_cents": discount, "invoice_left_cents": regular, "reserve_cents": reserve,
                        "borrow_cents": borrowed, "payoff_cents": stress["repay_cents"], "payday_cash_cents": cash,
                        "entry_lamports": entry, "pledge_lamports": q["pledge_lamports"], "setup_lamports": q["setup_lamports"],
                        "temporary_lamports": q["temporary_lamports"], "entry_slot": q.get("entry_slot"),
                        "network_lamports": q["fee_lamports"], "opening_fee_cents": opening_fee, "extra_cents": extra,
                        "cost_cents": cash_cost + stress["cost_cents"], "same_rate_cost_cents": common_cost,
                        "rate_bps": stress["rate_bps"], "retained_cents": q["retained_raw"] // scale, "slot": q["slot"],
                        "refused_slot": q["refused_slot"], "remainder_slot": q["remainder_slot"],
                        "fits": cash >= 0 and entry <= funds})
    viable = [row for row in options if row["fits"]]
    winner = min(viable, key=lambda row: (row["cost_cents"], row["entry_lamports"], row["advance_cents"]), default=None)
    choice = winner and winner["advance_cents"]
    return {"options": options, "choice": choice, "next_change": next_fee_change(options, fee, funds, choice), "wallet_lamports": funds,
            "holdback": quotes[0]["holdback"], "bill_cents": reserve_credit.BILL_CENTS}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replay three fuel loans with the same customer terms and next-stop budget")
    parser.add_argument("evidence", type=Path, nargs="?")
    args = parser.parse_args()
    quotes = replay(json.loads(args.evidence.read_bytes())) if args.evidence else saved()
    print(json.dumps(compare(quotes, "both", 100, 60000000, 0), indent=2))
