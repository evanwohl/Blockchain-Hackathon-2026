import hashlib
import json
from decimal import Decimal, ROUND_CEILING
from functools import cache

from freightos.capture import ROOT
from freightos.fuel_markets import fuel_levels, settlement_budget
from hedging.invoice_policy import invoice_policy, reserve_limit
from hedging.payment_timing import invoice_delay_gap, late_invoice_calendar, later_refill_calendar, reserve_deadline, spendable_on
from hedging.quick_pay import capacity_boundary, price_calendar

DATA = ROOT / "artifacts/quick_pay"
LOAD = "FOS-2048"
PROFILES = ("quick pay", "credit line", "cash on hand")
ROAD_CASH = "road cash"
ROAD_FEE_ALLOWANCE = 100
BACKUPS = {"quick pay": "invoice backup", "credit line": "bank backup", ROAD_CASH: "road cash backup"}
DELAY_DAYS = 7
LATE_PLANS = {profile: profile + " late freight" for profile in BACKUPS}
BACKUPS.update({late: late + " backup" for late in LATE_PLANS.values()})
SETTLED_FUEL = "reported settlement"
REFILL_LATER = "later reserve refill"
BACKUPS[REFILL_LATER] = "later reserve refill backup"


def funding_plan(sample, evidence, scenario, profile):
    g = evidence["gemini"]
    bill = sample["bills"][0]
    base_cents = bill["cents"] - g["extra_fuel_cents_interval"][1]
    if scenario == "fuel budget":
        amount = base_cents
    elif scenario == SETTLED_FUEL:
        g = settlement_budget(evidence)
        amount = base_cents + g["extra_fuel_cents_interval"][1]
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


def public_fuel_comparison(decision, plans):
    approved = plans[decision["profile"]][decision["scenario"]]
    exact = plans[decision["profile"]][SETTLED_FUEL]
    terms = {"load": approved["load"], "bill_cents": approved["bill_cents"], "reserve_cents": approved["payment_cents"],
             "outside_cents": approved["other_funds_cents"], "fees_cents": approved["cost_cents"],
             "comparison_fees_cents": approved["alternative_cost_cents"], "input_sha256": approved["input_sha256"]}
    if any(decision[key] != value for key, value in terms.items()):
        raise ValueError("The reconstructed plan differs from the approved payment")
    for path in ("artifacts/quick_pay/sample_calendar.json", "artifacts/gemini_ladder/active.json",
                 "artifacts/quick_pay/gemini_settlements.json"):
        if hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != decision["input_sha256"][path]:
            raise ValueError("Fuel evidence differs from the signed input hashes")
    g = exact["gemini"]
    return {"original_cents": plans[decision["profile"]]["fuel budget"]["bill_cents"],
            "approved_cents": approved["bill_cents"], "estimate_cents": exact["bill_cents"],
            "reserve_cents": exact["payment_cents"], "outside_cents": exact["other_funds_cents"],
            "approved_reserve_cents": approved["payment_cents"], "scenario": SETTLED_FUEL, "profile": decision["profile"],
            "settlement": g["settled_reference"], "band_floor": g["lower_inclusive"], "strike": g["stress_level"], "gallons": g["gallons"]}


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


def next_trip_options(plans, setup, wallet_raw):
    fixed = LATE_PLANS["quick pay"]
    reserve = next(s for s in plans[fixed]["oil stress"]["calendar"]["funding"] if s["id"] == "reserve redemption")
    day = reserve["restore_day"]
    options = []
    for profile in (REFILL_LATER, fixed, BACKUPS[fixed]):
        deposit = setup.get(profile)
        if deposit is None:
            continue
        choices = plans[profile]
        elections = {frozenset((e["invoice"], e["option"]) for e in p["invoice_elections"]) for p in choices.values()}
        if choices.keys() != plans[fixed].keys() or len(elections) != 1:
            continue
        cash = wallet_raw // 10000 - deposit
        capacities = [spendable_on(p["calendar"], p["invoice_elections"], p["funding"], day)
                      for p in choices.values()]
        options.append({"profile": profile, "capacity_cents": cash + min(capacities),
                        "peak_cost_cents": max(p["cost_cents"] for p in choices.values())})
    options.sort(key=lambda row: (row["peak_cost_cents"], -row["capacity_cents"]))
    apy = Decimal(plans[REFILL_LATER]["oil stress"]["refill_policy"]["apy"])
    return {"day": day, "budgets": len(plans[fixed]), "options": options,
            "return_yield_bps": period_yield_bps(apy, day - 1)}


def period_yield_bps(apy, days):
    factor = (1 + apy) ** (Decimal(days) / 365) - 1
    return int((factor * 10000).to_integral_value(rounding=ROUND_CEILING))


def refill_cost(evidence, days):
    path = "artifacts/quick_pay/vault_metrics.json"
    raw = (ROOT / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence["input_sha256"][path]:
        raise ValueError("Kamino yield snapshot differs from the funding evidence")
    metrics = json.loads(raw)
    rate = Decimal(metrics["metrics"]["apyActual"])
    if not rate.is_finite() or rate < 0 or metrics["observed"] != evidence["as_of"]:
        raise ValueError("Invalid recorded reserve yield")
    return {"apy": str(rate), "extra_term_bps": period_yield_bps(rate, days),
            "observed": metrics["observed"]}


@cache
def demo_plans(policy_version=2):
    if type(policy_version) is not int or policy_version not in (1, 2):
        raise ValueError("Unknown fuel policy version")
    evidence = json.loads((DATA / "case_report.json").read_bytes())
    sample = json.loads((DATA / "sample_calendar.json").read_bytes())
    levels = fuel_levels(evidence)
    anchor = evidence["gemini"]["stress_level"]
    if levels[anchor]["extra_fuel_cents_interval"] != evidence["gemini"]["extra_fuel_cents_interval"]:
        raise ValueError("Gemini threshold does not reproduce the original fuel budget")
    scenarios = ["fuel budget", "oil stress", *("wti-" + level for level in levels if level != anchor)]
    if policy_version == 2:
        scenarios.append(SETTLED_FUEL)
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
            plan["cash_policy"] = {"version": policy_version, "fee_allowance_cents": allowance,
                                   "reserve_limit_cents": policy["peak_reserve_cents"]}
            plans[profile][key] = plan
    refill = refill_cost(evidence, DELAY_DAYS)
    for regular, late in [*LATE_PLANS.items(), ("quick pay", REFILL_LATER)]:
        delayed = {}
        for key, original in plans[regular].items():
            case = late_invoice_calendar(original["calendar"], "primary load", DELAY_DAYS)
            if late == REFILL_LATER:
                case = later_refill_calendar(case, "reserve redemption", DELAY_DAYS, refill["extra_term_bps"])
            delayed[key] = priced_plan(case, late, key, original["gemini"], original["input_sha256"])
        policy = invoice_policy(delayed)
        if policy is None:
            raise ValueError("No shared invoice terms cover the delayed payment")
        policies[late] = policy
        plans[late] = {}
        for key, p in delayed.items():
            plan = priced_plan(p["calendar"], late, key, p["gemini"], p["input_sha256"], policy["elections"])
            plans[late][key] = {**plan, "late_freight": {"regular_profile": regular, "days": DELAY_DAYS}}
            if policy_version == 2:
                plans[late][key]["late_freight"]["version"] = policy_version
            if late == REFILL_LATER:
                plans[late][key]["refill_policy"] = {"version": 1, **refill}
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
    for regular, late in LATE_PLANS.items():
        for key, p in plans[regular].items():
            gap = invoice_delay_gap(p["calendar"], p["invoice_elections"], p["funding"], "primary load", DELAY_DAYS)
            comparison = {**gap, "days": DELAY_DAYS, "regular": regular, "alternative": late}
            if regular == "quick pay":
                comparison["flexible"] = REFILL_LATER
                edges[REFILL_LATER][key]["invoice_delay"] = comparison
            edges[regular][key]["invoice_delay"] = edges[late][key]["invoice_delay"] = comparison
    return plans, edges, levels, policies
