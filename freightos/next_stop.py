"""Preview a fuel payment from the remainders of the two settled Devnet examples."""

import gzip
import json
from datetime import datetime, timezone
from functools import cache

from freightos import devnet, public_offer
from hedging.quick_pay import integer

SNAPSHOT = devnet.SAMPLE.with_name("next_stop.json.gz")


@cache
def payment():
    run = json.loads(public_offer.SAMPLE.read_bytes())
    return public_offer.OfferPayment(run)


def amounts(fuel_cents, road_cents):
    if not integer(fuel_cents, "fuel bill"):
        raise ValueError("Enter a fuel bill greater than zero")
    integer(road_cents, "cash for the road")


def preview(first, second, fuel_cents, road_cents, rpc):
    amounts(fuel_cents, road_cents)
    plan = payment()
    scale = second["decision"]["raw_units_per_cent"]
    original = first["shares_minted_raw"] - first["shares_burned_raw"]
    offer = second["shares_minted_raw"] - second["shares_burned_raw"]
    if (first["share_account"] != second["share_account"] or second["share_account"] != str(plan.shares)
            or scale != first["decision"]["raw_units_per_cent"] or first["receipt"] == second["receipt"]
            or first["slot"] >= second["steps"]["prepare"]["slot"]
            or first["shares_after_raw"] != original or second["shares_before_setup_raw"] != original
            or min(original, offer) < 0 or second["shares_after_raw"] != original + offer):
        raise ValueError("The two public reserve balances do not reconcile")
    budget = devnet.redemption_quote(plan, second["steps"]["settle"]["slot"], original + offer, scale,
                                     "all", None, rpc)
    fuel = fuel_cents * scale
    road = road_cents * scale
    available = budget["return_raw"]
    result = {"fuel_cents": fuel_cents, "road_cents": road_cents, "raw_units_per_cent": scale,
              "first_shares_raw": original, "offer_shares_raw": offer, "eligible_shares_raw": budget["eligible_shares_raw"],
              "wallet_after_settlement_raw": second["wallet_after_settlement_raw"],
              "available_raw": available, "slot": budget["slot"], "vendor_raw": 0, "wallet_increase_raw": 0,
              "shortfall_raw": max(0, fuel + road - available)}
    if result["shortfall_raw"]:
        return result
    units = budget["eligible_shares_raw"]
    withdraw = devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, units, venue=devnet.VENUE)
    pay = public_offer.transfer(plan.cash, plan.payer, plan.destination, fuel)
    slot, keys, value = devnet.simulate_instructions(plan, [withdraw, pay], budget["slot"], rpc)

    def flow(account, mint, owner):
        before, after = devnet.token_balances(value, keys, account, mint, owner)
        return before, after - before

    owned, shares = flow(plan.shares, devnet.SHARES, plan.payer)
    _, cash = flow(plan.cash, devnet.MINT, plan.payer)
    _, vendor = flow(plan.destination, devnet.MINT, plan.payee)
    _, custody = flow(plan.custody, devnet.MINT, plan.vault_authority)
    if (owned != budget["wallet_shares_raw"] or shares != -units or vendor != fuel or cash < road
            or cash + vendor != -custody or cash + vendor != available):
        raise ValueError("The simulated fuel payment and retained wallet cash do not reconcile")
    return {**result, "slot": slot, "vendor_raw": vendor, "wallet_increase_raw": cash,
            "network_fee_lamports": value["fee"]}


@cache
def saved():
    sample = json.loads(gzip.decompress(SNAPSHOT.read_bytes()))
    calls = iter(sample["calls"])

    def replay(method, params):
        record = next(calls, None)
        if not record or method != record["method"] or params != record["params"]:
            raise ValueError("The saved simulation differs from the requested instructions")
        return record["response"]

    result = preview(devnet.verify(json.loads(devnet.SAMPLE.read_bytes())),
                     public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes())), 2000, 1000, replay)
    if next(calls, None) is not None:
        raise ValueError("Unexpected extra calls in the saved simulation")
    return {**result, "source": "saved", "checked_at": sample["checked_at"]}


def observed(result):
    return {**result, "source": "public-rpc", "checked_at": datetime.now(timezone.utc).isoformat()}
