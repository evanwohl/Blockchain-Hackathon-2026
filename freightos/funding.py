import json
from functools import cache

from freightos.capture import ROOT
from freightos.fuel_markets import fuel_levels
from hedging.invoice_policy import invoice_policy, reserve_limit
from hedging.payment_timing import reserve_deadline
from hedging.quick_pay import capacity_boundary, price_calendar

DATA = ROOT / "artifacts/quick_pay"
LOAD = "FOS-2048"
PROFILES = ("quick pay", "credit line", "cash on hand")
ROAD_CASH = "road cash"
ROAD_FEE_ALLOWANCE = 100
BACKUPS = {"quick pay": "invoice backup", "credit line": "bank backup", ROAD_CASH: "road cash backup"}


def funding_plan(sample, evidence, scenario, profile):
    g = evidence["gemini"]
    bill = sample["bills"][0]
    base_cents = bill["cents"] - g["extra_fuel_cents_interval"][1]
    if scenario == "fuel budget":
        amount = base_cents
    elif scenario == "oil stress":
        amount = bill["cents"]
    elif isinstance(scenario, str) and scenario.startswith("wti-") and scenario[4:] in fuel_levels(evidence):
        g = {**g, **fuel_levels(evidence)[scenario[4:]]}
        amount = base_cents + g["extra_fuel_cents_interval"][1]
    else:
        raise ValueError("Unknown fuel scenario")
    if profile not in PROFILES:
        raise ValueError("Unknown funding option")
    reserve = {**sample["funding"][0], "net_capacity_cents": 59999}
    alternatives = []
    if profile == "credit line":
        alternatives.append(
            {
                "id": "approved bank line",
                "available_day": 1,
                "restore_day": 28,
                "net_capacity_cents": 80000,
                "fixed_cost_cents": 0,
                "term_cost_bps": 100,
            }
        )
    case = {
        **sample,
        "bills": [{**bill, "cents": amount}],
        "opening_cash_cents": amount if profile == "cash on hand" else 0,
    }
    return priced_plan({**case, "funding": [reserve, *alternatives]}, profile, scenario, g, evidence["input_sha256"])


def priced_plan(case, profile, scenario, gemini, input_digest, elections=None):
    offers = case["funding"]
    selected = case
    if elections is not None:
        selected = {**case, "invoices": [
            {**invoice, "options": [o for o in invoice["options"] if o["name"] == elections[invoice["id"]]]}
            for invoice in case["invoices"]
        ]}
    decision = price_calendar(selected)
    fallback = price_calendar({**case, "funding": [s for s in offers if s["id"] != "reserve redemption"]})
    amount = case["bills"][0]["cents"]
    draw = sum(
        d["net_proceeds_cents"]
        for d in decision["funding"]
        if d["source"] == "reserve redemption"
    )
    return {
        "load": LOAD,
        "profile": profile,
        "scenario": scenario,
        "bill_cents": amount,
        "payment_cents": draw,
        "other_funds_cents": amount - draw,
        "cost_cents": decision["cost_cents"],
        "alternative_cost_cents": fallback["cost_cents"],
        "savings_cents": fallback["cost_cents"] - decision["cost_cents"],
        "funding": decision["funding"],
        "invoice_elections": decision["payments"],
        "gemini": gemini,
        "input_sha256": input_digest,
        "calendar": case,
    }


def pairing_value(plan):
    case = plan["calendar"]
    reserve = next(source for source in case["funding"] if source["id"] == "reserve redemption")
    standard_return = [
        {**invoice, "options": [o for o in invoice["options"] if o["name"] == "standard"]}
        if invoice["id"] == "backhaul" else invoice for invoice in case["invoices"]
    ]
    restricted = {**case, "invoices": standard_return}
    base = price_calendar({**restricted, "funding": []})["cost_cents"]
    reserve_only = price_calendar({**restricted, "funding": [reserve]})["cost_cents"]
    outside = plan["alternative_cost_cents"]
    best = min(reserve_only, outside)
    gain = best - plan["cost_cents"]
    boundary = None
    if gain > 0:
        boundary = capacity_boundary(case, reserve["id"], plan["payment_cents"],
                                     {"cost_cents": plan["cost_cents"]}, {"cost_cents": outside},
                                     {"cost_cents": best})
    return {"base_cents": base, "reserve_only_cents": reserve_only, "outside_only_cents": outside,
            "joint_cents": plan["cost_cents"], "pairing_savings_cents": gain, "boundary": boundary}


@cache
def demo_plans():
    evidence = json.loads((DATA / "case_report.json").read_bytes())
    sample = json.loads((DATA / "sample_calendar.json").read_bytes())
    levels = fuel_levels(evidence)
    anchor = evidence["gemini"]["stress_level"]
    if levels[anchor]["extra_fuel_cents_interval"] != evidence["gemini"]["extra_fuel_cents_interval"]:
        raise ValueError("Gemini threshold does not reproduce the original fuel budget")
    scenarios = ["fuel budget", "oil stress", *("wti-" + level for level in levels if level != anchor)]
    plans = {profile: {scenario: funding_plan(sample, evidence, scenario, profile) for scenario in scenarios}
             for profile in PROFILES}
    policies = {"quick pay": invoice_policy(plans["quick pay"])}
    credit = plans["credit line"]
    for profile, allowance in [("credit line", 0), (ROAD_CASH, ROAD_FEE_ALLOWANCE)]:
        policy = invoice_policy(credit, fee_allowance_cents=allowance)
        if policy is None:
            raise ValueError("No shared invoice choice satisfies the fee limit")
        policies[profile] = {**policy, "fee_allowance_cents": allowance}
        plans[profile] = {}
        for key, p in credit.items():
            case = reserve_limit(p["calendar"], policy["peak_reserve_cents"])
            plan = priced_plan(case, profile, key, p["gemini"], p["input_sha256"], policy["elections"])
            plan["cash_policy"] = {"version": 1, "fee_allowance_cents": allowance,
                                   "reserve_limit_cents": policy["peak_reserve_cents"]}
            plans[profile][key] = plan
    for profile, backup in BACKUPS.items():
        plans[backup] = {}
        for key, original in plans[profile].items():
            case = reserve_limit(original["calendar"], 0)
            plan = priced_plan(case, backup, key, original["gemini"], original["input_sha256"])
            plan["backup_for"] = profile
            plans[backup][key] = plan
    edges = {profile: {key: pairing_value(plan) for key, plan in choices.items()} for profile, choices in plans.items()}
    for profile, backup in BACKUPS.items():
        for key, plan in plans[profile].items():
            deadline = reserve_deadline(plan["calendar"], plan["invoice_elections"], plan["funding"], "reserve redemption")
            if deadline:
                edges[profile][key]["timing"] = {
                    **deadline, "backup_profile": backup,
                    "extra_fee_cents": plans[backup][key]["cost_cents"] - plan["cost_cents"],
                }
    return plans, edges, levels, policies
