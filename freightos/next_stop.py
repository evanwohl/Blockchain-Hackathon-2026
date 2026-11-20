"""Preview a fuel payment from the remainders of the two settled Devnet examples."""

import gzip
import json
from datetime import datetime, timezone
from fractions import Fraction
from functools import cache
from math import ceil

from freightos import devnet, fuel_cover, fuel_update, public_offer
from hedging.quick_pay import integer

SNAPSHOT = devnet.SAMPLE.with_name("next_stop.json.gz")
FUEL_BUFFERS = devnet.SAMPLE.with_name("fuel_buffers.json.gz")
ADVANCE_CHECKS = devnet.SAMPLE.with_name("advance_reserve.json.gz")
TWO_STOPS = devnet.SAMPLE.with_name("two_stops.json.gz")


@cache
def payment():
    run = json.loads(public_offer.SAMPLE.read_bytes())
    return public_offer.OfferPayment(run)


def amounts(fuel_cents, road_cents):
    if not integer(fuel_cents, "fuel bill"):
        raise ValueError("Enter a fuel bill greater than zero")
    integer(road_cents, "cash for the road")


def check_fuel_inputs(gallons, pump_mills, oil_rise):
    if not integer(gallons, "gallons for later", 100_000):
        raise ValueError("Enter at least one gallon for the next stop")
    if pump_mills is not None and not integer(pump_mills, "diesel price", 999_999):
        raise ValueError("Enter a positive diesel price")
    if type(oil_rise) is not bool:
        raise ValueError("Invalid oil scenario")


def fuel_holdback(gallons, market, *, pump_mills=None, oil_rise=False):
    check_fuel_inputs(gallons, pump_mills, oil_rise)
    trip_gallons = int(market["gallons"])
    if trip_gallons <= 0 or market["marked_cents"] <= 0:
        raise ValueError("A positive trip estimate is required to price future gallons")
    per_gallon = Fraction(market["marked_cents"], trip_gallons) if pump_mills is None else Fraction(pump_mills, 10)
    base = ceil(gallons * per_gallon)
    rise = max(0, Fraction(market["threshold"]) - Fraction(market["close"])) if oil_rise else 0
    added_per_gallon = rise * Fraction(market["cents_per_oil_dollar"]) / trip_gallons
    cents = ceil(gallons * (per_gallon + added_per_gallon))
    return {"gallons": gallons, "cost_cents": cents, "base_cents": base, "extra_cents": cents - base,
            "trip_gallons": trip_gallons, "trip_cents": market["marked_cents"], "pump_mills": pump_mills,
            "oil_rise": oil_rise, "threshold": market["threshold"], "oil_close": market["close"],
            "event": market["close_ticker"], "scenario_event": market["target_ticker"], "checked_at": market["checked_at"]}


def reserve_budget(first, second, rpc):
    scale = second["decision"]["raw_units_per_cent"]
    original = first["shares_minted_raw"] - first["shares_burned_raw"]
    offer = second["shares_minted_raw"] - second["shares_burned_raw"]
    if (first["share_account"] != second["share_account"] or second["share_account"] != str(payment().shares)
            or scale != first["decision"]["raw_units_per_cent"] or first["receipt"] == second["receipt"]
            or first["slot"] >= second["steps"]["prepare"]["slot"]
            or first["shares_after_raw"] != original or second["shares_before_setup_raw"] != original
            or min(original, offer) < 0 or second["shares_after_raw"] != original + offer):
        raise ValueError("The two public reserve balances do not reconcile")
    budget = devnet.redemption_quote(payment(), second["steps"]["settle"]["slot"], original + offer, scale,
                                     "all", None, rpc)
    return {**budget, "first_shares_raw": original, "offer_shares_raw": offer}


def preview(first, second, fuel_cents, road_cents, rpc, *, holdback=None, keep_shares=False):
    amounts(fuel_cents, road_cents)
    plan = payment()
    budget = reserve_budget(first, second, rpc)
    scale = budget["raw_units_per_cent"]
    fuel = fuel_cents * scale
    road = road_cents * scale
    available = budget["return_raw"]
    result = {"fuel_cents": fuel_cents, "road_cents": road_cents, "raw_units_per_cent": scale,
              "first_shares_raw": budget["first_shares_raw"], "offer_shares_raw": budget["offer_shares_raw"], "eligible_shares_raw": budget["eligible_shares_raw"],
              "wallet_after_settlement_raw": second["wallet_after_settlement_raw"],
              "available_raw": available, "slot": budget["slot"], "vendor_raw": 0, "wallet_increase_raw": 0,
              "shortfall_raw": max(0, fuel + road - available)}
    if holdback is None:
        if result["shortfall_raw"]:
            return result
        if keep_shares:
            return {**result, **check_split(plan, budget, fuel, road, rpc)}
        payment_check, _, _ = check_payment(plan, budget, fuel, road, rpc)
        return {**result, **payment_check}

    needed = holdback["cost_cents"] * scale
    upper = min(fuel, max(0, (available - road - needed) // scale) * scale)
    result.update(holdback=holdback, shortfall_raw=max(0, fuel + road + needed - available),
                  budget_limit_cents=upper // scale, retained_raw=None)
    if not upper:
        return result
    for _ in range(2):
        split = check_split(plan, budget, upper, road, rpc)
        deficit = needed - split["retained_raw"]
        if deficit <= 0:
            return {**result, **split, "budget_limit_cents": upper // scale, "shortfall_raw": fuel - upper,
                    "covered_pump_mills": (split["retained_raw"] // scale) * 10 // holdback["gallons"]}
        upper -= ((deficit + scale - 1) // scale) * scale
        if upper <= 0:
            break
        budget = {**budget, "slot": split["slot"]}
    raise ValueError("The reserve could not confirm both withdrawals at the requested budget")


def check_split(plan, budget, upper, road, rpc):
    sized = devnet.sized_redemption(plan, budget, upper + road, rpc)
    check, instructions, initial = check_payment(plan, sized, upper, road, rpc)
    leftover = budget["eligible_shares_raw"] - sized["shares_used_raw"]
    if not leftover:
        return {**check, "retained_raw": 0, "retained_shares_raw": 0}
    remaining = devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, leftover, venue=devnet.VENUE)
    slot, keys, value = devnet.simulate_instructions(plan, [*instructions, remaining], check["slot"], rpc, trace=True)
    final = balances(plan, keys, value)
    if any(final[key][0] != initial[key][0] for key in initial):
        raise ValueError("Wallet or vault state changed between previews")
    retained = withdrawal_proceeds(value, 3, plan)
    if (withdrawal_proceeds(value, 1, plan) != sized["return_raw"]
            or retained != final["cash"][1] - initial["cash"][1] or final["shares"][1] != initial["shares"][1] - leftover
            or final["vendor"][1] != upper or final["custody"][1] != initial["custody"][1] - retained):
        raise ValueError("The remaining reserve could not cover the later fuel stop")
    return {**check, "slot": slot, "retained_raw": retained, "retained_shares_raw": leftover}


def withdrawal_proceeds(value, index, plan):
    group, = [row for row in value["innerInstructions"] if row["index"] == index]
    transfer, = [ix["parsed"]["info"] for ix in group["instructions"]
                 if ix["programId"] == str(devnet.TOKEN) and ix.get("parsed", {}).get("type") == "transferChecked"
                 and ix["parsed"]["info"]["destination"] == str(plan.cash)]
    if (transfer["mint"] != str(devnet.MINT) or transfer["source"] != str(plan.custody)
            or transfer["authority"] != str(plan.vault_authority) or transfer["tokenAmount"]["decimals"] != 6):
        raise ValueError("The withdrawal differs from the quoted vault and wallet")
    return int(transfer["tokenAmount"]["amount"])


def balances(plan, keys, value):
    accounts = {"shares": (plan.shares, devnet.SHARES, plan.payer),
                "cash": (plan.cash, devnet.MINT, plan.payer),
                "vendor": (plan.destination, devnet.MINT, plan.payee),
                "custody": (plan.custody, devnet.MINT, plan.vault_authority)}
    result = {}
    for key, identity in accounts.items():
        before, after = devnet.token_balances(value, keys, *identity)
        result[key] = before, after - before
    return result


def check_payment(plan, budget, fuel, road, rpc):
    units = budget["shares_used_raw"]
    withdraw = devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, units, venue=devnet.VENUE)
    pay = public_offer.transfer(plan.cash, plan.payer, plan.destination, fuel)
    instructions = [withdraw, pay]
    slot, keys, value = devnet.simulate_instructions(plan, instructions, budget["slot"], rpc)
    state = balances(plan, keys, value)
    cash = state["cash"][1]
    if (state["shares"] != (budget["wallet_shares_raw"], -units) or state["vendor"][1] != fuel
            or cash < road or cash + fuel != -state["custody"][1] or cash + fuel != budget["return_raw"]):
        raise ValueError("The simulated fuel payment and retained wallet cash do not reconcile")
    return {"slot": slot, "vendor_raw": fuel, "wallet_increase_raw": cash,
            "network_fee_lamports": value["fee"]}, instructions, state


def replay_preview(sample, fuel, road, *, holdback=None, keep_shares=False, check_genesis=False):
    calls = iter(sample["calls"])

    def replay(method, params):
        record = next(calls, None)
        if not record or method != record["method"] or params != record["params"]:
            raise ValueError("The saved simulation differs from the requested instructions")
        return record["response"]

    if (keep_shares or check_genesis) and replay("getGenesisHash", []) != devnet.GENESIS:
        raise ValueError("The recorded reserve check requires public Devnet")
    result = preview(devnet.verify(json.loads(devnet.SAMPLE.read_bytes())),
                     public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes())),
                     fuel, road, replay, holdback=holdback, keep_shares=keep_shares)
    if next(calls, None) is not None:
        raise ValueError("Unexpected extra calls in the saved simulation")
    return {**result, "source": "saved", "checked_at": sample["checked_at"]}


@cache
def saved(two_stops=False):
    sample = json.loads(gzip.decompress((TWO_STOPS if two_stops else SNAPSHOT).read_bytes()))
    return replay_preview(sample, 1500 if two_stops else 2000, 500 if two_stops else 1000,
                          holdback=fuel_holdback(3, fuel_update.saved()) if two_stops else None)


@cache
def saved_buffer(case):
    cover = fuel_cover.saved()
    if case == "cash":
        fuel = cover["cash_only_cents"]
    elif case == "late" and cover["position"]:
        fuel = cover["position"]["late_payout_gap_cents"]
    else:
        raise ValueError("No recorded reserve check matches this fuel plan")
    samples = json.loads(gzip.decompress(FUEL_BUFFERS.read_bytes()))
    return {**replay_preview(samples[case], fuel, 0, keep_shares=True), "buffer_case": case}


@cache
def advance_recordings():
    return json.loads(gzip.decompress(ADVANCE_CHECKS.read_bytes()))


def advance_reserve(cents, live=False, rpc=devnet.request, *, evidence=None):
    integer(cents, "reserve payment", 19000)
    if not cents:
        raise ValueError("This plan does not need a reserve payment")
    hold = fuel_holdback(3, fuel_update.saved(), oil_rise=True)
    if live:
        if rpc("getGenesisHash", []) != devnet.GENESIS:
            raise ValueError("Use public Devnet to check the reserve")
        first = devnet.verify(json.loads(devnet.SAMPLE.read_bytes()), rpc)
        second = public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes()), rpc)
        result = observed(preview(first, second, cents, 0, rpc, holdback=hold))
    else:
        sample = (advance_recordings() if evidence is None else evidence).get(str(cents))
        if sample is None:
            raise ValueError("Use a live reserve check for this amount")
        result = replay_preview(sample, cents, 0, holdback=hold, check_genesis=True)
    if (result["vendor_raw"] != cents * result["raw_units_per_cent"] or result["retained_raw"] is None
            or result["retained_raw"] < hold["cost_cents"] * result["raw_units_per_cent"]):
        raise ValueError("The reserve cannot pay that amount and protect the next stop at this check")
    return result


def observed(result):
    return {**result, "source": "public-rpc", "checked_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Check the reserve portion of a partial freight advance")
    parser.add_argument("evidence", type=Path, nargs="?")
    parser.add_argument("--advance-reserve-cents", type=int, default=1927)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.live and args.evidence:
        parser.error("Choose a downloaded recording or a live check")
    evidence = json.loads(args.evidence.read_bytes()) if args.evidence else None
    print(json.dumps(advance_reserve(args.advance_reserve_cents, args.live, evidence=evidence), indent=2))
