import argparse
import gzip
import hashlib
import json
import os
from contextlib import suppress
from functools import cache, lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from freightos.capture import ROOT
from freightos.fork import ReserveFork, VaultExecutionError
from freightos import offer_funding
from freightos import atomic_fuel, flash_exit, bill, pledge_yield, collateral_path, credit_exit, customer_credit, devnet, driver_desk, diesel_model, load_credit, mainnet_loan, mainnet_stop, forward, freight_credit, fuel_cash, fuel_cover, fuel_floor, fuel_swap, fuel_update, next_stop, partial_payment, payday_policy, public_offer, regional_diesel, repeat_credit, split_payment, replay_prices, reserve_credit, reserve_return, road_cash, stop_backup, trip_cover
from freightos.surcharge import larger_offer_budget, offer_decision, partial_offer, standard_terms_threshold, surcharge_plans
from freightos.live import reserve_observation
from freightos.funding import DATA, DELAY_DAYS, LOAD, demo_plans, funding_plan, next_trip_options, pairing_value, public_fuel_comparison, refill_cost
from freightos.load_calendar import funding_options, project as project_load
from hedging.quick_pay import integer, price_calendar
from hedging.payment_timing import invoice_delay_gap, late_invoice_calendar, later_refill_calendar, reserve_deadline

ASSETS = Path(__file__).with_name("web")


@lru_cache(maxsize=16)
def recorded_trip(inputs, road):
    reserve = None
    if road is not None:
        saved = next_stop.saved()
        reserve = {"available_cents": saved["available_raw"] // saved["raw_units_per_cent"],
                   "road_cents": road, "slot": saved["slot"]}
    return trip_cover.analyze(trip_cover.recorded(), dict(inputs), reserve=reserve)


@cache
def trial_data():
    report = json.loads((DATA / "carrier_trial.json").read_bytes())
    cases = {row["load"]: row for row in report.pop("rows")}
    report["loads"] = [
        {"load": load, "used_reserve": bool(row["fork_receipt"]),
         "conventional_savings_cents": row["conventional_savings_cents"],
         "composition_savings_cents": row["composition_savings_cents"]}
        for load, row in cases.items()
    ]
    return report, cases


def verified_sample(load):
    from freightos.verify_receipt import replay

    case = trial_data()[1].get(load)
    if case is None or not case["fork_receipt"]:
        raise ValueError("This sample has no signed reserve payment")
    proof = replay(case["fork_receipt"])
    return {"load": load, "signature": case["fork_receipt"]["signature"], **proof}


@cache
def stressed_trial():
    _, cases = trial_data()
    comparisons = {"conventional": [], "composition": []}
    timing = {"misses": 0, "shortfall_cents": 0, "replanning_fee_cents": 0}
    freight_delay = {"days": DELAY_DAYS, "short_cases": 0, "peak_gaps_cents": 0, "replanning_fee_cents": 0}
    refill = refill_cost(json.loads((DATA / "case_report.json").read_bytes()), DELAY_DAYS)
    flexible = {"cheaper_cases": 0, "savings_cents": 0, "extra_term_bps": refill["extra_term_bps"]}
    for row in cases.values():
        case = row["case"]
        reserve = case["funding"][0]
        selected = row["decision"]
        gap = invoice_delay_gap(case, selected["payments"], selected["funding"], "primary load", DELAY_DAYS)
        late_case = late_invoice_calendar(case, "primary load", DELAY_DAYS)
        fixed_cost = selected["cost_cents"]
        if gap["extra_cash_cents"]:
            revised = price_calendar(late_case)
            if revised["status"] != "funded":
                raise ValueError("No delayed-freight plan covers this trial load")
            freight_delay["short_cases"] += 1
            freight_delay["peak_gaps_cents"] += gap["extra_cash_cents"]
            fixed_cost = revised["cost_cents"]
            freight_delay["replanning_fee_cents"] += fixed_cost - selected["cost_cents"]
        deferred = price_calendar(later_refill_calendar(late_case, reserve["id"], DELAY_DAYS, refill["extra_term_bps"]))
        if deferred["status"] == "funded" and deferred["cost_cents"] < fixed_cost:
            flexible["savings_cents"] += fixed_cost - deferred["cost_cents"]
            flexible["cheaper_cases"] += 1
        cutoff = reserve_deadline(case, selected["payments"], selected["funding"], reserve["id"])
        if cutoff and cutoff["arrival_day"] + 1 > cutoff["last_day"]:
            timing["misses"] += 1
            timing["shortfall_cents"] += cutoff["shortfall_cents"]
            offers = [{**s, "latest_available_day": cutoff["arrival_day"] + 1} if s["id"] == reserve["id"] else s
                      for s in case["funding"]]
            alternative = price_calendar({**case, "funding": offers})
            timing["replanning_fee_cents"] += alternative["cost_cents"] - selected["cost_cents"]
        delayed = {**reserve, "available_day": reserve["available_day"] + 1,
                   "fixed_cost_cents": reserve["fixed_cost_cents"] + 500,
                   "term_cost_bps": reserve["term_cost_bps"] + 50}
        conventional = row["conventional"]["cost_cents"]
        simple_reserve = price_calendar({**case, "funding": [delayed]})
        simple = min(conventional, simple_reserve["cost_cents"]) if simple_reserve["status"] == "funded" else conventional
        joint = row["adverse_plan"]["cost_cents"]
        comparisons["conventional"].append(conventional - joint)
        comparisons["composition"].append(simple - joint)
    counts = {}
    for name, values in comparisons.items():
        if min(values) < 0:
            raise ValueError("Expanded funding menu has higher modeled cost")
        k = (len(values) + 9) // 10
        counts[name] = {"savings_cents": sum(values), "positive_cases": sum(x > 0 for x in values),
                        "replaced_cases": k, "worst_mix_cents": sum(sorted(values)[:-k]) + k * min(values)}
    return {**counts, "one_day_delay": timing, "late_freight": freight_delay, "flexible_refill": flexible}


@cache
def sample_load_reserve():
    # A separate hypothetical reserve; never deposit the editable opening cash.
    return ReserveFork().available_after_deposit(60_000) // 10_000


class LoadSettlement:
    def __init__(self):
        self.lock = RLock()
        self.fork = ReserveFork()
        self.deposit_receipt = None
        self.manual_payment = None
        self.record_version = uuid4().hex
        self.quote_balances = None
        self.returns = {}
        plans, edges, self.levels, policies = demo_plans()
        self.plans, self.edges, self.policies = plans.copy(), edges.copy(), policies.copy()
        self.surcharge_profile = None
        self.applied_quote = None
        self.surcharge_limit = None

    def load_reserve(self, mode):
        if mode in (None, "off"):
            return None
        if mode not in ("sample", "wallet"):
            raise ValueError("Choose a practice reserve")
        with self.lock:
            terms = next(s for s in self.plans["quick pay"]["oil stress"]["calendar"]["funding"]
                         if s["id"] == "reserve redemption")
            try:
                if mode == "sample":
                    capacity = sample_load_reserve()
                elif self.fork.pending_review():
                    return {"capacity_cents": 0, "note": "The practice wallet has a pending payment."}
                else:
                    capacity = self.fork.available_after_deposit(0) // 10_000
            except VaultExecutionError:
                return {"capacity_cents": 0, "note": "Redemption could not be confirmed."}
            return {"capacity_cents": capacity, "fixed_cost_cents": terms["fixed_cost_cents"],
                    "term_cost_bps": terms["term_cost_bps"]}

    def clear_surcharge(self):
        for group in (self.plans, self.edges, self.policies):
            group.pop(self.surcharge_profile, None)
        self.surcharge_profile = self.applied_quote = self.surcharge_limit = None
        self.quote_balances = None

    def quote_surcharge(self, data):
        if self.fork.pending_review() or self.manual_payment or LOAD in self.fork.receipts:
            raise ValueError("The fuel bill is locked to its payment plan")
        if data.get("version") != self.record_version:
            raise ValueError("This bill changed. Reopen the surcharge check.")
        scenario = data.get("scenario")
        base = self.plans["quick pay"].get(scenario)
        if not base:
            raise ValueError("Select a fuel estimate")
        wallet = self.fork.balance(self.fork.cash)
        floor = integer(data.get("road_cash_cents", 0), "cash to keep for the road")
        cash = wallet // 10000
        return_needed = max(0, floor - cash)
        if return_needed:
            withdrawal = reserve_return.return_for_cash(self.fork, return_needed)
            cash_after_return = (wallet + withdrawal["return_usdc_raw"]) // 10000
            cap = withdrawal["remaining_value_raw"] // 10000
        else:
            cash_after_return = cash
            cap = self.fork.available_after_deposit(cash - floor) // 10000
        available = min(cap, base["calendar"]["funding"][0]["net_capacity_cents"])
        limit = integer(data["reserve_limit_cents"], "reserve limit") if "reserve_limit_cents" in data else None
        cap = available if limit is None else min(available, limit)
        cents, day = data.get("cents"), data.get("day")
        due = base["calendar"]["bills"][0]["day"]
        offer = None
        allow_partial = data.get("allow_partial", False)
        if type(allow_partial) is not bool or (allow_partial and "regular_day" not in data):
            raise ValueError("Select partial payment only for a customer early-pay offer")
        if "regular_day" in data or "fee_cents" in data:
            regular = data.get("regular_day")
            if regular is None:
                raise ValueError("Enter the regular payment day")
            plans, policy, offer = offer_decision(cents, day, regular, data.get("fee_cents"), cap, scenario)
            if allow_partial:
                option = partial_offer(cents, day, regular, data["fee_cents"], cap, scenario)
                offer["partial_checked"] = True
                if option:
                    baseline_setup = self.fork.deposit_quote(policy["peak_reserve_cents"])
                    baseline_cost = plans[scenario]["cost_cents"]
                    baseline_early = offer["take_offer"]
                    plans, policy, partial = option
                    setup = self.fork.deposit_quote(policy["peak_reserve_cents"])
                    offer.update(partial={**partial, "setup_difference_cents": setup - baseline_setup,
                                          "baseline_early": baseline_early, "baseline_cost_cents": baseline_cost,
                                          "wallet_after_cents": cash_after_return - setup},
                                 take_offer=True, early_cost_cents=plans[scenario]["cost_cents"],
                                 least_saved_cents=partial["min_savings_cents"]["waiting"])
            if cap < available and not offer["take_offer"]:
                offer["larger_budget"] = larger_offer_budget(cents, day, regular, data["fee_cents"], cap, available, scenario)
            other_day = regular if offer["take_offer"] else day
            other_cost = offer["wait_cost_cents"] if offer["take_offer"] else offer["early_cost_cents"]
        else:
            plans, policy = surcharge_plans(cents, day, cap)
            other_day = due + 1 if day <= due else due
            other_plans, _ = surcharge_plans(cents, other_day, cap)
            other_cost = other_plans[scenario]["cost_cents"]
        plan = plans[scenario]
        quote = {**plan["surcharge"], "profile": plan["profile"], "scenario": scenario, "due": due,
                 "bill_cents": plan["bill_cents"], "cost_cents": plan["cost_cents"],
                 "reserve_cents": plan["payment_cents"], "comparison_day": other_day,
                 "comparison_cost_cents": other_cost,
                 "setup_cents": self.fork.deposit_quote(policy["peak_reserve_cents"]),
                 "threshold_cents": standard_terms_threshold(cap),
                 "requested_limit_cents": limit, "available_reserve_cents": available,
                 "budget_count": len(plans),
                 "full_invoices": all(o == "standard" for o in policy["elections"].values()),
                 "record_version": self.record_version}
        if offer is not None:
            alternative = offer.get("larger_budget")
            if alternative:
                try:
                    alternative["setup_cents"] = self.fork.deposit_quote(alternative["peak_reserve_cents"])
                except VaultExecutionError:
                    offer["larger_budget"] = None
                else:
                    alternative["extra_setup_cents"] = alternative["setup_cents"] - quote["setup_cents"]
            quote["offer"] = offer
        if floor:
            wallet_after = cash_after_return - quote["setup_cents"]
            if wallet_after < floor or (return_needed and quote["setup_cents"]):
                raise ValueError("The wallet and reserve cannot cover both the road money and this plan")
            quote.update(road_cash_cents=floor, wallet_after_cents=wallet_after, wallet_return_cents=return_needed)
        return {"id": bill.commitment(quote).hex(), **quote}

    def check_road_cash(self, plan, deposit_cents=0):
        floor = plan.get("road_cash_cents", 0)
        if floor and self.fork.balance(self.fork.cash) // 10000 - deposit_cents < floor:
            raise ValueError("Return the road money to your wallet before using this plan")

    def setup_quotes(self):
        balances = self.fork.balance(self.fork.cash), self.fork.balance(self.fork.shares)
        if balances != self.quote_balances:
            quotes = {}
            by_target = {}
            for profile in self.plans:
                target = self.reserve_target(profile)
                if target in by_target:
                    quotes[profile] = by_target[target]
                    continue
                try:
                    quotes[profile] = self.fork.deposit_quote(target)
                except VaultExecutionError:
                    quotes[profile] = None
                by_target[target] = quotes[profile]
            if self.surcharge_profile:
                profile = self.surcharge_profile
                floor = self.plans[profile]["oil stress"].get("road_cash_cents", 0)
                if quotes[profile] and balances[0] // 10000 - quotes[profile] < floor:
                    quotes[profile] = None
            self.return_limits = {}
            self.quotes = quotes
            self.quote_balances = balances
        return self.quotes

    def next_trip_check(self, setup, wallet, shares):
        check = next_trip_options(self.plans, setup, wallet)
        for row in check["options"]:
            available = 0
            if shares and setup[row["profile"]] == 0:
                target = self.reserve_target(row["profile"])
                if target not in self.return_limits:
                    try:
                        quote = reserve_return.return_quote(self.fork, target, {})
                        self.return_limits[target] = quote["return_usdc_raw"]
                    except VaultExecutionError:
                        pass
                available = self.return_limits.get(target, 0)
            row["returnable_cents"] = (wallet + available) // 10000 - wallet // 10000
        return check

    def state(self):
        with self.lock:
            wallet = self.fork.balance(self.fork.cash)
            shares = self.fork.balance(self.fork.shares)
            setup = {} if self.manual_payment or LOAD in self.fork.receipts else self.setup_quotes()
            review = self.fork.pending_review()
            return {
                "load": LOAD,
                "slot": self.fork.slot,
                "reserve_shares_raw": shares,
                "wallet_usdc_raw": wallet,
                "setup_cents": setup,
                "next_trip": self.next_trip_check(setup, wallet, shares) if setup and not review else None,
                "deposit": self.deposit_receipt,
                "last_return": self.latest_return(),
                "manual_payment": self.manual_payment,
                "record_version": self.record_version,
                "surcharge_profile": self.surcharge_profile,
                "surcharge_limit_cents": self.surcharge_limit,
                "receipt": self.fork.receipts.get(LOAD),
                "review": review,
                "plans": self.plans["quick pay"],
                "funding_plans": self.plans,
                "pairing_values": self.edges,
                "market_levels": self.levels,
                "invoice_policies": self.policies,
            }

    def latest_return(self):
        if self.returns:
            key = next(reversed(self.returns))
            receipt = self.returns[key]
            return {"id": key, "signature": receipt["signature"], "quote": receipt["quote"],
                    "wallet_after_raw": receipt["wallet_after_raw"]}

    def return_quote(self, data):
        profile = data.get("profile")
        if data.get("version") != self.record_version or profile not in self.plans:
            raise ValueError("This bill changed. Check the available amount again.")
        if self.fork.pending_review():
            raise ValueError("Finish or cancel the pending vendor payment first")
        settled = self.fork.receipts.get(LOAD)
        entry = self.manual_payment
        locked = settled["decision"] if settled else entry
        if locked and locked["profile"] != profile:
            raise ValueError("Use the funding plan on this bill")
        target = 0 if locked else self.reserve_target(profile)
        context = {
            "profile": profile, "load": LOAD, "version": self.record_version,
            "payment_signature": settled["signature"] if settled else None}
        if "extra_wallet_cents" in data:
            quote = reserve_return.return_for_cash(self.fork, data["extra_wallet_cents"], target, context)
        else:
            quote = reserve_return.return_quote(self.fork, target, context)
        return {"id": bill.commitment(quote).hex(), **quote}

    def reserve_target(self, profile):
        target = max(p["payment_cents"] for p in self.plans[profile].values())
        return max(target, self.policies.get(profile, {}).get("peak_reserve_cents", 0))

    def act(self, action, data):
        with self.lock:
            if action == "reset":
                if data.get("version", self.record_version) != self.record_version:
                    raise ValueError("This demo session has changed")
                if not self.manual_payment and LOAD not in self.fork.receipts:
                    raise ValueError("Finish the demo before starting a new session")
                self.fork = ReserveFork()
                self.deposit_receipt = None
                self.manual_payment = None
                self.returns.clear()
                self.clear_surcharge()
                self.quote_balances = None
                self.record_version = uuid4().hex
                return {"reset": True}
            if action == "quote-surcharge":
                return self.quote_surcharge(data)
            if action == "apply-surcharge":
                if self.applied_quote and data.get("id") == self.applied_quote:
                    return {"profile": self.surcharge_profile}
                quote = self.quote_surcharge(data)
                if quote["id"] != data.get("id"):
                    raise ValueError("Check the surcharge plan again before using it")
                plans, policy = surcharge_plans(quote["cents"], quote["day"], quote["capacity_cents"],
                                                quote.get("full_fee_cents", quote.get("fee_cents", 0)), quote.get("regular_day"),
                                                quote.get("advance_cents"))
                if quote.get("road_cash_cents"):
                    plans = {key: {**plan, "road_cash_cents": quote["road_cash_cents"]} for key, plan in plans.items()}
                edges = {key: pairing_value(plan) for key, plan in plans.items()}
                self.clear_surcharge()
                key = quote["profile"]
                self.plans[key], self.edges[key], self.policies[key] = plans, edges, policy
                self.surcharge_profile, self.applied_quote = key, quote["id"]
                self.surcharge_limit = quote["requested_limit_cents"]
                self.record_version = uuid4().hex
                return {"profile": key}
            if action == "return-quote":
                return self.return_quote(data)
            if action == "return-reserve":
                key = data.get("id")
                if key in self.returns:
                    return self.returns[key]
                quote = self.return_quote(data)
                if quote.pop("id") != key or not quote["return_shares_raw"]:
                    raise ValueError("Review the available return before moving money")
                receipt = reserve_return.execute(self.fork, quote)
                self.returns[key] = receipt
                self.record_version = uuid4().hex
                return receipt
            if action in ("return-receipt", "verify-return"):
                latest = self.latest_return()
                if not latest or data.get("signature") != latest["signature"]:
                    raise ValueError("Reopen the latest wallet return")
                receipt = next(reversed(self.returns.values()))
                return receipt if action == "return-receipt" else reserve_return.replay_return(receipt)
            if action in ("record-payment", "undo-record"):
                if data.get("version") != self.record_version:
                    raise ValueError("This bill has changed. Review its current balance.")
                if action == "undo-record":
                    self.manual_payment = None
                    self.record_version = uuid4().hex
                    return {"removed": True}
                profile, scenario = data.get("profile"), data.get("scenario")
                plan = self.plans.get(profile, {}).get(scenario)
                if not plan or self.fork.pending_review():
                    raise ValueError("Review the current bill before recording a payment")
                receipt = self.fork.receipts.get(LOAD)
                if receipt and receipt["decision"] != plan:
                    raise ValueError("Use the plan on the reserve receipt")
                self.check_road_cash(plan)
                if plan["payment_cents"] and not receipt:
                    raise ValueError("Complete the reserve payment first")
                entry = {"profile": profile, "scenario": scenario, "cents": plan["other_funds_cents"]}
                reference = data.get("reference", "")
                if not isinstance(reference, str) or len(reference) > 80 or (reference and not reference.isprintable()):
                    raise ValueError("Use up to 80 printable characters for the payment reference")
                if reference.strip():
                    entry["reference"] = reference.strip()
                if self.manual_payment and self.manual_payment != entry:
                    raise ValueError("An outside payment is already recorded")
                self.manual_payment = entry
                return entry
            if self.manual_payment and action in ("deposit", "review"):
                raise ValueError("This bill is already covered")
            if action == "cancel":
                return self.fork.cancel(data.get("approval"))
            if action == "check-vendor":
                return self.fork.check_vendor(data.get("approval"))
            if action == "deposit":
                if data:
                    profile = data.get("profile")
                    cap = data.get("max_cents")
                    if profile not in self.plans or type(cap) is not int or cap < 0 or data.get("version") != self.record_version:
                        raise ValueError("Review the current reserve amount")
                    if LOAD in self.fork.receipts:
                        raise ValueError("This load is already settled")
                    amount = self.setup_quotes()[profile]
                    if amount is None:
                        raise ValueError("Reserve setup is unavailable for this funding plan")
                    self.check_road_cash(self.plans[profile]["oil stress"], amount)
                    if amount > cap:
                        raise ValueError("Reserve setup exceeds the approved amount")
                    if amount:
                        if self.fork.pending_review():
                            raise ValueError("Finish or cancel the pending payment review")
                        self.deposit_receipt = self.fork.deposit(amount * 10000)
                    return self.deposit_receipt or {"deposited": False}
                if not self.deposit_receipt:
                    if self.surcharge_profile and self.plans[self.surcharge_profile]["oil stress"].get("road_cash_cents"):
                        raise ValueError("Review the reserve setup for your selected plan")
                    self.deposit_receipt = self.fork.deposit()
                return self.deposit_receipt
            if action == "review":
                scenario, profile = (
                    data.get("scenario"),
                    data.get("profile", "quick pay"),
                )
                if profile not in self.plans or scenario not in self.plans[profile]:
                    raise ValueError("Unknown payment plan")
                plan = self.plans[profile][scenario]
                self.check_road_cash(plan)
                if not plan["payment_cents"]:
                    if plan.get("backup_for"):
                        raise ValueError("Pay the vendor directly using the selected backup")
                    raise ValueError("Available cash covers the bill")
                return self.fork.review(LOAD, plan["payment_cents"] * 10000, plan)
            if action == "approve":
                return self.fork.approve(data.get("approval"))
            if action == "verify":
                from freightos.verify_receipt import replay

                if LOAD not in self.fork.receipts:
                    raise ValueError("Settle the bill before verifying")
                return replay(self.fork.receipts[LOAD])
            raise ValueError("Unknown action")


class PublicCheck:
    def __init__(self, observe, wait_seconds=0):
        self.wait_seconds = wait_seconds
        self.observe = observe
        self.lock = Lock()
        self.valid_until = 0
        self.observation = None

    def read(self):
        if not self.lock.acquire(timeout=self.wait_seconds):
            raise ValueError("Public check is in progress")
        try:
            if monotonic() >= self.valid_until or self.observation is None:
                self.observation = self.observe()
                self.valid_until = monotonic() + 30
            return self.observation
        finally:
            self.lock.release()


def allowed_demo_request(host, origin, port):
    loopback = (f"localhost:{port}", f"127.0.0.1:{port}")
    if host in loopback and origin in (None, f"http://{host}"):
        return True
    name = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if os.environ.get("CODESPACES") != "true" or not name or not domain:
        return False
    public_host = f"{name}-{port}.{domain}"
    return origin == f"https://{public_host}" and host in (*loopback, "localhost", "127.0.0.1", public_host)


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content_type, body, *, static=False):
        cache_control = "no-cache" if static else "no-store"
        etag = None
        if static:
            etag = f'W/"{hashlib.sha256(body).hexdigest()}"'
            validators = self.headers.get("If-None-Match", "").split(",")
            if any(value.strip() in (etag, etag[2:], "*") for value in validators):
                status = 304
            if content_type == "font/woff2":
                cache_control = "private, max-age=86400"
        compressible = len(body) >= 1024 and content_type != "font/woff2"
        self.send_response(status)
        self.send_header("Cache-Control", cache_control)
        if etag:
            self.send_header("ETag", etag)
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        if status == 304:
            self.end_headers()
            return
        encodings = self.headers.get("Accept-Encoding", "").lower().split(",")
        if compressible and any(value.strip() == "gzip" for value in encodings):
            body = gzip.compress(body, compresslevel=4, mtime=0)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        with suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(body)

    def respond(self, status, obj):
        self.reply(status, "application/json", json.dumps(obj, separators=(",", ":")).encode())

    def do_GET(self):
        if self.path == "/api/forward-fill":
            self.respond(200, forward.published_score())
            return
        if self.path == "/api/forward-fill/check":
            try:
                self.respond(200, self.server.forward_check.read())
            except (ValueError, OSError):
                self.respond(503, {"error": "The outcome check is unavailable. Try again shortly."})
            return
        if self.path == "/evidence/forward-fill":
            self.respond(200, {"ticket": forward.saved(), "funding": forward.funding(), "backup_forecast": forward.registered_backup(), "ticket_sha256": forward.TICKET_HASH})
            return
        if self.path == "/api/trial/stress":
            self.respond(200, stressed_trial())
            return
        if self.path == "/api/trial":
            self.respond(200, trial_data()[0])
            return
        if self.path.startswith("/api/trial/"):
            key = self.path.removeprefix("/api/trial/")
            if key.endswith("/verify"):
                try:
                    self.respond(200, verified_sample(key.removesuffix("/verify")))
                except ValueError as exc:
                    self.respond(400, {"error": str(exc)})
                return
            row = trial_data()[1].get(key)
            if row is None:
                self.respond(404, {"error": "Unknown load"})
            else:
                self.respond(200, row)
            return
        if self.path == "/evidence/carrier-trial":
            self.reply(
                200, "application/json", (DATA / "carrier_trial.json").read_bytes()
            )
            return
        if self.path == "/evidence/reserve-yield":
            self.reply(200, "application/json", (DATA / "vault_metrics.json").read_bytes())
            return
        if self.path == "/evidence/gemini-settlements":
            self.reply(200, "application/json", (DATA / "gemini_settlements.json").read_bytes())
            return
        if self.path == "/api/public-payment/fuel":
            try:
                receipt = devnet.verify(json.loads(devnet.SAMPLE.read_bytes()))
                self.respond(200, public_fuel_comparison(receipt["decision"], self.server.settlement.plans))
            except (OSError, ValueError, KeyError, TypeError):
                self.respond(503, {"error": "The fuel calculation could not be checked against the signed bill."})
            return
        if self.path == "/evidence/gemini":
            self.reply(
                200,
                "application/json",
                (ROOT / "artifacts/gemini_ladder/active.json").read_bytes(),
            )
            return
        if self.path in ("/api/next-stop", "/api/two-stops", "/api/fuel-buffer/cash", "/api/fuel-buffer/late"):
            try:
                example = (next_stop.saved_buffer(self.path.rsplit("/", 1)[1]) if self.path.startswith("/api/fuel-buffer/")
                           else next_stop.saved(self.path == "/api/two-stops"))
                self.respond(200, example)
            except (OSError, ValueError, KeyError, TypeError):
                self.respond(503, {"error": "The saved next-stop example is unavailable."})
            return
        if self.path in ("/evidence/next-stop", "/evidence/two-stops", "/evidence/fuel-buffers", "/evidence/advance-reserve"):
            snapshot = {"/evidence/two-stops": next_stop.TWO_STOPS, "/evidence/next-stop": next_stop.SNAPSHOT,
                        "/evidence/fuel-buffers": next_stop.FUEL_BUFFERS, "/evidence/advance-reserve": next_stop.ADVANCE_CHECKS}[self.path]
            self.reply(200, "application/json", gzip.decompress(snapshot.read_bytes()), static=True)
            return
        if self.path == "/api/trip-cover":
            self.respond(200, {**trip_cover.saved(), "source": "recorded"})
            return
        if self.path == "/evidence/pledge-yield":
            self.reply(200, "application/json", gzip.decompress(pledge_yield.EVIDENCE.read_bytes()), static=True)
            return
        if self.path in partial_payment.EVIDENCE:
            sample = partial_payment.EVIDENCE[self.path]
            self.reply(200, "application/json", gzip.decompress(sample.read_bytes()), static=True)
            return
        if self.path in ("/api/midtrip-payment", "/api/midtrip-payment/simulate"):
            try:
                result = self.server.midtrip_payment.read() if self.path.endswith("/simulate") else partial_payment.saved()
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                self.respond(503, {"error": "The partial repayment could not be verified."})
            return
        if self.path in ("/evidence/collateral-exit", "/evidence/cash-exit"):
            sample = flash_exit.MIXED_SAMPLE if self.path.endswith("cash-exit") else flash_exit.SAMPLE
            self.reply(200, "application/json", gzip.decompress(sample.read_bytes()), static=True)
            return
        if self.path in ("/api/collateral-exit", "/api/collateral-exit/simulate", "/api/cash-exit", "/api/cash-exit/simulate"):
            try:
                mixed = self.path.startswith("/api/cash-exit")
                if self.path.endswith("/simulate"):
                    result = (self.server.cash_exit if mixed else self.server.flash_exit).read()
                else:
                    result = flash_exit.saved_mixed() if mixed else flash_exit.saved()
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                self.respond(503, {"error": "The collateral-funded payoff could not be verified."})
            return
        if self.path == "/evidence/split-payment":
            self.reply(200, "application/json", gzip.decompress(split_payment.SAMPLE.read_bytes()), static=True)
            return
        if self.path in ("/api/split-payment", "/api/split-payment/simulate"):
            try:
                result = self.server.split_payment.read() if self.path.endswith("/simulate") else split_payment.saved()
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                self.respond(503, {"error": "The combined Devnet loan and sale could not be verified."})
            return
        if self.path == "/evidence/repeat-credit":
            self.reply(200, "application/json", gzip.decompress(repeat_credit.SAMPLE.read_bytes()), static=True)
            return
        if self.path in ("/api/repeat-credit", "/api/repeat-credit/simulate"):
            try:
                result = self.server.repeat_credit.read() if self.path.endswith("/simulate") else repeat_credit.saved()
                self.respond(200, result)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, IndexError):
                self.respond(503, {"error": "The Devnet repeat-loan simulation could not be verified."})
            return
        if self.path == "/evidence/settled-credit":
            self.reply(200, "application/json", gzip.decompress(load_credit.SAMPLE.read_bytes()), static=True)
            return
        if self.path in ("/api/settled-credit", "/api/settled-credit/verify"):
            try:
                proof = self.server.settled_credit.read() if self.path.endswith("/verify") else load_credit.settled()
                self.respond(200, proof)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                self.respond(503, {"error": "The two loan transactions could not be verified."})
            return
        if self.path == "/evidence/credit-exits":
            self.reply(200, "application/json", gzip.decompress(credit_exit.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path == "/evidence/gemini-collateral-paths":
            self.reply(200, "application/json", gzip.decompress(collateral_path.EVIDENCE.read_bytes()), static=True)
            return
        if self.path == "/evidence/borrower-setup":
            self.reply(200, "application/json", mainnet_loan.SETUP_EVIDENCE.read_bytes(), static=True)
            return
        if self.path == "/evidence/mainnet-fuel-lending":
            self.reply(200, "application/json", mainnet_loan.EVIDENCE.read_bytes(), static=True)
            return
        if self.path == "/evidence/mainnet-fuel-pools":
            self.reply(200, "application/json", mainnet_stop.EVIDENCE.read_bytes(), static=True)
            return
        if self.path == "/evidence/fuel-floor":
            self.reply(200, "application/json", gzip.decompress(fuel_floor.SAMPLE.read_bytes()), static=True)
            return
        if self.path == "/evidence/fuel-cash":
            self.reply(200, "application/json", gzip.decompress(fuel_cash.SAMPLE.read_bytes()), static=True)
            return
        if self.path == "/evidence/stop-backup":
            self.reply(200, "application/json", gzip.decompress(stop_backup.SAMPLE.read_bytes()), static=True)
            return
        if self.path == "/evidence/sol-payday":
            self.reply(200, "application/json", gzip.decompress(payday_policy.HISTORY.read_bytes()), static=True)
            return
        if self.path == "/evidence/fuel-swap":
            self.reply(200, "application/json", gzip.decompress(fuel_swap.SAMPLE.read_bytes()), static=True)
            return
        if self.path == "/evidence/customer-credit":
            self.reply(200, "application/json", gzip.decompress(customer_credit.SAMPLE.read_bytes()), static=True)
            return
        if self.path in ("/evidence/atomic-fuel", "/evidence/atomic-late"):
            artifact = atomic_fuel.LATE_SAMPLE if self.path.endswith("-late") else atomic_fuel.SAMPLE
            self.reply(200, "application/json", gzip.decompress(artifact.read_bytes()), static=True)
            return
        if self.path == "/evidence/reserve-credit":
            self.reply(200, "application/json", gzip.decompress(reserve_credit.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path == "/evidence/fuel-credit":
            self.reply(200, "application/json", gzip.decompress((freight_credit.DATA / "credit_examples.json.gz").read_bytes()), static=True)
            return
        if self.path == "/evidence/diesel-model":
            self.reply(200, "application/json", gzip.decompress(diesel_model.AUDIT.read_bytes()), static=True)
            return
        if self.path == "/evidence/fill-prices":
            self.reply(200, "application/json", gzip.decompress(replay_prices.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path == "/evidence/regional-diesel":
            self.reply(200, "application/json", gzip.decompress(regional_diesel.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path == "/evidence/trip-cover":
            self.reply(200, "application/json", gzip.decompress(trip_cover.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path == "/evidence/fuel-cover":
            self.reply(200, "application/json", gzip.decompress(fuel_cover.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path in ("/api/fuel-cover", "/api/fuel-cover/refresh"):
            refresh = self.path.endswith("/refresh")
            try:
                comparison = self.server.fuel_cover.read() if refresh else fuel_cover.saved()
                self.respond(200, {**comparison, "source": "gemini-api" if refresh else "saved"})
            except (OSError, ValueError, KeyError, ArithmeticError, TypeError, RuntimeError):
                self.respond(503, {"error": "The Gemini contract and order book could not be checked."})
            return
        if self.path == "/evidence/offer-fuel-update":
            self.reply(200, "application/json", gzip.decompress(fuel_update.SNAPSHOT.read_bytes()), static=True)
            return
        if self.path in ("/api/public-offer/fuel", "/api/public-offer/fuel/refresh"):
            try:
                refresh = self.path.endswith("/refresh")
                fuel = self.server.offer_fuel.read() if refresh else fuel_update.saved()
                self.respond(200, {**fuel, "source": "gemini-api" if refresh else "saved"})
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
                self.respond(503, {"error": "The Gemini fuel update is unavailable."})
            return
        if self.path == "/evidence/public-offer":
            self.reply(200, "application/json", public_offer.SAMPLE.read_bytes(), static=True)
            return
        if self.path in ("/api/public-offer", "/api/public-offer/verify"):
            try:
                proof = (self.server.public_offer.read() if self.path.endswith("/verify")
                         else public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes())))
                self.respond(200, proof)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                self.respond(503, {"error": "The public payment check is unavailable. Retry or view the signed transaction."})
            return
        if self.path in ("/api/public-payment", "/api/public-payment/verify"):
            try:
                result = (self.server.public_payment.read() if self.path.endswith("/verify")
                          else devnet.verify(json.loads(devnet.SAMPLE.read_bytes())))
                self.respond(200, result)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                self.respond(503, {"error": "Could not verify the receipt. Retry or open the transaction on Explorer."})
            return
        if self.path in ("/api/public-payment/return/road", "/api/public-payment/return/all"):
            portion = self.path.rsplit("/", 1)[-1]
            try:
                self.respond(200, self.server.return_previews[portion].read())
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                self.respond(503, {"error": "The public reserve quote is unavailable. Try again shortly."})
            return
        if self.path == "/api/reserve-observation":
            try:
                self.respond(200, self.server.public_reserve.read())
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                self.respond(503, {"error": "Public reserve data is unavailable. You can retry the check."})
            return
        if self.path == "/api/state":
            self.respond(200, self.server.settlement.state())
            return
        assets = {
            "/": ("overview.html", "text/html; charset=utf-8"),
            "/fuel-desk": ("fuel_desk.html", "text/html; charset=utf-8"),
            "/collateral-exit.js": ("collateral_exit.js", "text/javascript"),
            "/repeat-run.js": ("repeat_run.js", "text/javascript"),
            "/fuel-desk.js": ("fuel_desk.js", "text/javascript"),
            "/fuel-desk.css": ("fuel_desk.css", "text/css"),
            "/funding_mix.js": ("funding_mix.js", "text/javascript"),
            "/fuel-stop": ("fuel_stop.html", "text/html; charset=utf-8"),
            "/fuel-stop.js": ("fuel_stop.js", "text/javascript"),
            "/fuel-stop.css": ("fuel_stop.css", "text/css"),
            "/fuel-credit": ("fuel_credit.html", "text/html; charset=utf-8"),
            "/payday_policy.js": ("payday_policy.js", "text/javascript"),
            "/fuel_swap.js": ("fuel_swap.js", "text/javascript"),
            "/customer_credit.js": ("customer_credit.js", "text/javascript"),
            "/fuel_credit.js": ("fuel_credit.js", "text/javascript"),
            "/forward-fill": ("forward.html", "text/html; charset=utf-8"),
            "/pump_value.js": ("pump_value.js", "text/javascript"),
            "/forward.js": ("forward.js", "text/javascript"),
            "/trip-cover": ("trip_cover.html", "text/html; charset=utf-8"),
            "/payday.js": ("payday.js", "text/javascript"),
            "/trip_credit.js": ("trip_credit.js", "text/javascript"),
            "/trip_cover.js": ("trip_cover.js", "text/javascript"),

            "/load": ("load.html", "text/html; charset=utf-8"),
            "/browser_wallet.js": ("browser_wallet.js", "text/javascript"),
            "/load_credit.js": ("load_credit.js", "text/javascript"),
            "/load.js": ("load.js", "text/javascript"),
            "/next-stop": ("next_stop.html", "text/html; charset=utf-8"),
            "/next_stop.js": ("next_stop.js", "text/javascript"),
            "/public-offer": ("public_offer.html", "text/html; charset=utf-8"),
            "/public_offer.js": ("public_offer.js", "text/javascript"),
            "/public-payment": ("public_payment.html", "text/html; charset=utf-8"),
            "/public_payment.js": ("public_payment.js", "text/javascript"),
            "/overview.js": ("overview.js", "text/javascript"),
            "/bill": ("index.html", "text/html; charset=utf-8"),
            "/trial": ("trial.html", "text/html; charset=utf-8"),
            "/trial.js": ("trial.js", "text/javascript"),
            "/workspace.css": ("workspace.css", "text/css"),
            "/style.css": ("style.css", "text/css"),
            "/app.js": ("app.js", "text/javascript"),
            "/wallet.js": ("wallet.js", "text/javascript"),
            "/bill_tabs.js": ("bill_tabs.js", "text/javascript"),
            "/media/freightos-mark.svg": ("media/freightos-mark.svg", "image/svg+xml"),
            "/media/instrument-serif-latin-400-italic.woff2": ("media/instrument-serif-latin-400-italic.woff2", "font/woff2"),
            "/media/barlow-condensed-latin-600-normal.woff2": ("media/barlow-condensed-latin-600-normal.woff2", "font/woff2"),
            "/media/inter-latin-wght-normal.woff2": ("media/inter-latin-wght-normal.woff2", "font/woff2"),
            "/surcharge.js": ("surcharge.js", "text/javascript"),
            "/next_trip.js": ("next_trip.js", "text/javascript"),
            "/payment_slips.js": ("payment_slips.js", "text/javascript"),
            "/common.js": ("common.js", "text/javascript"),
        }
        asset = self.path.partition("?")[0]
        if asset not in assets:
            self.respond(404, {"error": "Page unavailable"})
            return
        name, mime = assets[asset]
        content = (ASSETS / name).read_bytes()
        if mime.startswith("text/html"):
            for fragment in ("funding_fields", "loan_receipt", "payment_slips"):
                marker = ("{{" + fragment + "}}").encode()
                if marker in content:
                    content = content.replace(marker, (ASSETS / (fragment + ".html")).read_bytes())
        self.reply(200, mime, content, static=True)

    def do_POST(self):
        host = self.headers.get("Host", "")
        if not allowed_demo_request(host, self.headers.get("Origin"), self.server.server_port):
            self.respond(403, {"error": "Local demo requests only"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if (
                self.headers.get_content_type() != "application/json"
                or not 0 < size <= 8192
            ):
                raise ValueError("Expected a small JSON request")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise TypeError("Expected a JSON object")
            if self.path == "/api/public-offer/funding":
                self.respond(200, offer_funding.compare(data.get("cash_cents"), data.get("reserve_available")))
                return
            if self.path == "/api/trip-credit/exit":
                collateral = integer(data.get("collateral_lamports"), "loan pledge", 10**10)
                advance = integer(data.get("advance_raw"), "loan proceeds", 10**10)
                days, drop, source = data.get("days"), data.get("drop"), data.get("source")
                freight_credit.terms(drop, days)
                if not collateral or not advance or source not in ("recorded", "live"):
                    raise ValueError("Choose a funded loan and payoff source")
                live = source == "live"
                if live and not self.server.trip_credit_lock.acquire(blocking=False):
                    self.respond(503, {"error": "Another loan check is running. Try again shortly."})
                    return
                try:
                    result = credit_exit.check(collateral, advance, drop, days, recorded=not live)
                    self.respond(200, {**result, "source": source})
                except (OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError, RuntimeError):
                    message = "Devnet could not verify the payoff." if live else "No verified saved payoff matches this draw and pledge."
                    self.respond(503, {"error": message})
                finally:
                    if live:
                        self.server.trip_credit_lock.release()
                return
            if self.path == "/api/trip-credit":
                gap, cap = data.get("gap_cents"), data.get("pledge_millis")
                drop, days = data.get("drop"), data.get("days")
                freight_credit.terms(drop, days)
                integer(gap, "fuel gap", 100_000_000)
                integer(cap, "SOL pledge limit", 1_000_000)
                offer = data.get("offer_cents")
                if offer is not None:
                    integer(offer, "other financing fee", 100_000_000)
                source = data.get("source", "live")
                if source not in ("live", "recorded"):
                    raise ValueError("Unknown loan source")
                destination = data.get("destination", "vendor")
                if destination not in ("vendor", "wallet"):
                    raise ValueError("Unknown loan destination")
                repay_limit = data.get("repay_limit_cents")
                if repay_limit is not None:
                    integer(repay_limit, "payday repayment limit", 100_000_000)
                    if destination != "wallet":
                        raise ValueError("This limit applies to a fuel wallet loan")
                if gap == 0:
                    raise ValueError("The trip has no fuel shortfall")
                if source == "live" and not self.server.trip_credit_lock.acquire(blocking=False):
                    self.respond(503, {"error": "Another loan check is running. Try again shortly."})
                    return
                try:
                    quote = freight_credit.trip_draw(gap, cap, drop, days, recorded=source == "recorded", destination=destination, repay_limit=repay_limit)
                    self.respond(200, {**quote, "comparison": freight_credit.compare_fee(quote, offer), "source": source})
                except (OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError, RuntimeError):
                    reason = ("No saved check matches these terms." if source == "recorded" else
                              "Devnet could not verify this loan. Your fuel gap is still open.")
                    self.respond(503, {"error": reason})
                finally:
                    if source == "live":
                        self.server.trip_credit_lock.release()
                return
            if self.path in ("/api/fuel-credit", "/api/reserve-credit", "/api/atomic-fuel", "/api/atomic-fuel/prepare"):
                drop, days, source = data.get("drop"), data.get("days"), data.get("source")
                freight_credit.terms(drop, days)
                if source not in ("live", "recorded"):
                    raise ValueError("Choose a supported credit preview")
                protect = data.get("protect_payday", False)
                if type(protect) is not bool:
                    raise ValueError("Choose a supported payday assumption")
                fee = integer(data.get("offer_fee_cents", 1000), "early payment fee", reserve_credit.INVOICE_CENTS)
                other = integer(data.get("other_cents", 0), "other borrowing costs", 100_000_000)
                if self.path == "/api/atomic-fuel/prepare" and (source != "live" or data.get("address") != str(freight_credit.OWNER)):
                    raise ValueError("Connect the demo carrier that owns the two reserve receipts")
                try:
                    if self.path == "/api/atomic-fuel/prepare":
                        quote = atomic_fuel.live(drop, days, prepare=True, protect=protect)
                    elif self.path == "/api/atomic-fuel":
                        quote = self.server.atomic_checks[drop, days, protect].read() if source == "live" else atomic_fuel.saved(drop, days, protect)
                    elif self.path == "/api/reserve-credit":
                        quote = self.server.reserve_credit_checks[drop, days].read() if source == "live" else reserve_credit.saved(drop, days)
                    else:
                        run = self.server.credit_checks[drop, days].read() if source == "live" else freight_credit.saved(drop, days)
                        quote = freight_credit.evaluate(run, drop, days)
                    if self.path.startswith("/api/atomic-fuel"):
                        decision = atomic_fuel.compare_offer(quote, fee, other, partial=data.get("partial", False),
                                                             reserve_fee_cents=data.get("reserve_fee_cents", 0))
                        if decision["partial"] is not None:
                            decision["partial"]["recorded"] = str(decision["partial"]["draw_cents"]) in next_stop.advance_recordings()
                        quote = {**quote, "offer": decision}
                        if decision["choice"] != "loan":
                            quote.pop("transaction", None)
                    self.respond(200, {**quote, "source": source})
                except (OSError, ValueError, KeyError, IndexError, TypeError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "The public network could not confirm the loan and fuel payment."})
                return
            if self.path == "/api/trip-cover":
                trip = {key: data.get(key) for key in trip_cover.EXAMPLE}
                trip.update({key: data[key] for key in ("region", "spend_limit_cents", "pump_cash_cents", "diesel_model") if key in data})
                trip_cover.validate(trip)
                road = data.get("road_cents")
                if road is not None:
                    integer(road, "road cash", 100_000_000)
                source = data.get("source", "live")
                if source not in ("live", "recorded"):
                    raise ValueError("Unknown market source")
                try:
                    if source == "recorded":
                        result = recorded_trip(tuple(sorted(trip.items())), road)
                    else:
                        snapshot = self.server.trip_books.read()
                        reserve = None
                        if road is not None:
                            budget = next_stop.reserve_budget(self.server.public_payment.read(), self.server.public_offer.read(), devnet.request)
                            reserve = {"available_cents": budget["return_raw"] // budget["raw_units_per_cent"],
                                       "road_cents": road, "slot": budget["slot"]}
                        result = trip_cover.analyze(snapshot, trip, reserve=reserve)
                    self.respond(200, {**result, "source": source})
                except diesel_model.CalibrationUnavailable as exc:
                    self.respond(422, {"error": str(exc)})
                except (OSError, ValueError, KeyError, ArithmeticError, TypeError, RuntimeError):
                    self.respond(503, {"error": "Could not verify the market comparison for this trip."})
                return
            if self.path == "/api/devnet-approval-preview":
                address = data.get("address")
                if not isinstance(address, str):
                    raise ValueError("Connect a wallet first")
                try:
                    self.respond(200, devnet.preview_approval(address))
                except (OSError, KeyError, TypeError):
                    self.respond(503, {"error": "Could not prepare the Devnet preview. Try again."})
                return
            if self.path in ("/api/load-credit", "/api/load-credit/payoff"):
                try:
                    if self.path.endswith("/payoff"):
                        result = load_credit.prepare_payoff(data["address"], data["signature"])
                    else:
                        result = load_credit.prepare(data["load"], data.get("address"), data["pledge_limit_lamports"])
                    self.respond(200, result)
                except (ValueError, TypeError) as exc:
                    self.respond(400, {"error": str(exc)})
                except (OSError, KeyError, IndexError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "Devnet could not verify these loan terms. Check the wallet and try again."})
                return
            if self.path == "/api/devnet-wallet":
                address = data.get("address")
                if not isinstance(address, str):
                    raise ValueError("Enter a wallet address")
                devnet.Pubkey.from_string(address)
                try:
                    self.respond(200, devnet.wallet_balances(address))
                except (OSError, ValueError, KeyError, TypeError):
                    self.respond(503, {"error": "Devnet balances are unavailable. Try again."})
                return
            if self.path == "/api/load-calendar":
                calendar = project_load(data)
                if data.get("compare_funding") is True:
                    calendar["decision"] = funding_options(
                        data, calendar, self.server.settlement.load_reserve(data.get("reserve_mode")))
                self.respond(200, calendar)
                return
            if self.path == "/api/midtrip-payment/preview":
                try:
                    terms = driver_desk.selected_topup(data) if "plan" in data else (partial_payment.selected_payment(data), 0)
                except RuntimeError:
                    self.respond(503, {"error": "The opening wallet or loan terms could not be verified."})
                    return
                source = data.get("source")
                if source not in ("recorded", "live"):
                    raise ValueError("Choose saved evidence or a public simulation")
                try:
                    result = (self.server.partial_previews(*terms).read() if source == "live" else partial_payment.saved(*terms))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    self.respond(503, {"error": "The selected partial payment could not be verified."})
                    return
                self.respond(200, {**result, "source": source})
                return
            if self.path == "/api/fuel-desk":
                try:
                    result = driver_desk.quote(data)
                except RuntimeError:
                    self.respond(503, {"error": "Recorded funding terms could not be verified."})
                    return
                self.respond(200, result)
                return
            if self.path == "/api/fuel-stop":
                args = [data.get(k) for k in ("budget", "cash", "received", "target", "stress")]
                preference = data.get("preference", "keep-cash")
                menu, fee = data.get("offer_menu", "partial"), data.get("offer_fee", 100)
                amount = data.get("offer_amount")
                road_cash.limits(*args, preference, menu, fee, amount)
                source = data.get("source")
                if source not in ("recorded", "live"):
                    raise ValueError("Choose recorded or live funding checks")
                live = source == "live"
                if live and not self.server.trip_credit_lock.acquire(blocking=False):
                    self.respond(503, {"error": "Another public check is running. Try again shortly."})
                    return
                try:
                    self.respond(200, road_cash.evaluate(*args, preference=preference, live=live, offer_menu=menu, offer_fee=fee, offer_amount=amount))
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "The pool prices and fuel funds could not be checked together."})
                finally:
                    if live:
                        self.server.trip_credit_lock.release()
                return
            if self.path == "/api/stop-backup":
                cap = integer(data.get("cap_lamports"), "wallet limit for fuel")
                reference = integer(data.get("reference_cents"), "SOL reference price", 10000000)
                floor = integer(data.get("floor_cents"), "payday cash target", 100000000)
                stress = data.get("stress_cents")
                if stress is not None:
                    integer(stress, "additional SOL price", 10000000)
                separate = data.get("separate_cents")
                if separate is not None:
                    integer(separate, "separate fuel cash", 100000000)
                receipts = data.get("receipt_amounts")
                if receipts is not None:
                    if separate is None or not isinstance(receipts, list) or not 1 <= len(receipts) <= 4:
                        raise ValueError("Choose the freight receipts to compare")
                    for received in receipts:
                        integer(received, "freight receipt", reserve_credit.INVOICE_CENTS)
                source = data.get("source")
                matches_recording = cap == stop_backup.DEFAULT_CAP and separate in (None, fuel_cash.SAMPLE_CASH)
                if not reference or source not in ("recorded", "live") or (source == "recorded" and not matches_recording):
                    raise ValueError("Choose a matching wallet limit and backup check")
                live = source == "live"
                if live and not self.server.trip_credit_lock.acquire(blocking=False):
                    self.respond(503, {"error": "A public funding check is running. Try again shortly."})
                    return
                try:
                    cases, _ = payday_policy.price_cases(reference, stress)
                    prices = [c["price_cents"] for c in cases]
                    if receipts:
                        q = fuel_floor.public_quotes(cap, separate, receipts[0], floor, max(prices), devnet.read_with_retry()) if live else fuel_floor.saved()
                    elif live:
                        q = stop_backup.observe(cap, devnet.read_with_retry(), outside_cents=separate)
                    else:
                        q = stop_backup.saved() if separate is None else fuel_cash.saved()
                    result = {**q, "payday": stop_backup.payday(q, prices, floor), "source": source}
                    if separate is not None:
                        result["cash_comparison"] = fuel_cash.compare(q, prices)
                    if receipts and q.get("market"):
                        try:
                            result["cash_floor"] = fuel_floor.decisions(q, receipts, floor, prices)
                        except ValueError:
                            result["cash_floor"] = None
                    if not live:
                        result["evidence_path"] = "/evidence/fuel-floor" if receipts else "/evidence/fuel-cash" if separate is not None else "/evidence/stop-backup"
                    result.pop("market", None)
                    self.respond(200, result)
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "Could not verify the fuel amount and protected reserve within the same market window."})
                finally:
                    if live:
                        self.server.trip_credit_lock.release()
                return
            if self.path == "/api/payday-policy":
                args = [data.get("menu"), data.get("fee_cents"), 0, data.get("extra_cents"), data.get("floor_cents"), data.get("stress_cents")]
                budget = integer(data.get("budget_cents"), "wallet budget", 100000000)
                keep = data.get("keep_profile", False)
                if type(keep) is not bool:
                    raise ValueError("Choose whether to leave loan setup money tied up")
                payday_policy.inputs(*args)
                source = data.get("source")
                if source not in ("live", "recorded"):
                    raise ValueError("Choose recorded or live funding checks")
                try:
                    checked = self.server.policy_checks[args[0]].read() if source == "live" else payday_policy.quotes(args[0])
                    self.respond(200, {**payday_policy.decide(checked, *args, budget_cents=budget, keep_profile=keep), "source": source})
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "The funding routes could not all be verified on the same wallet and market window."})
                return
            if self.path == "/api/fuel-swap":
                customer = integer(data.get("customer_cents"), "customer contribution", 9900)
                menu, fee, funds, extra = (data.get(k) for k in ("menu", "fee_cents", "wallet_lamports", "extra_cents"))
                customer_credit.inputs(menu, fee, funds, extra)
                price = data.get("price_cents")
                if price is not None:
                    integer(price, "replacement price", 10000000)
                source = data.get("source")
                if customer not in customer_credit.MENUS[menu] or source not in ("recorded", "live"):
                    raise ValueError("Choose an available customer payment and price source")
                try:
                    quote = self.server.swap_checks[customer].read() if source == "live" else fuel_swap.saved(customer)
                    self.respond(200, {**fuel_swap.compare(quote, fee, funds, extra, price), "source": source})
                except (OSError, ValueError, KeyError, TypeError, RuntimeError, ArithmeticError):
                    self.respond(503, {"error": "Could not verify the pool prices, wallet and vendor payment together."})
                return
            if self.path == "/api/customer-credit":
                args = (data.get("menu"), data.get("fee_cents"), data.get("wallet_lamports"), data.get("extra_cents"))
                customer_credit.inputs(*args)
                source = data.get("source")
                if source not in ("recorded", "live"):
                    raise ValueError("Choose recorded or live loan terms")
                try:
                    quotes = self.server.customer_credit.read() if source == "live" else customer_credit.saved()
                    self.respond(200, {**customer_credit.compare(quotes, *args), "source": source})
                except (OSError, ValueError, KeyError, TypeError, ArithmeticError, RuntimeError):
                    self.respond(503, {"error": "Could not verify all three loans and the protected reserve."})
                return
            if self.path == "/api/advance-reserve":
                source = data.get("source")
                if source not in ("recorded", "live"):
                    raise ValueError("Choose recorded or live evidence")
                try:
                    result = next_stop.advance_reserve(data.get("draw_cents"), live=source == "live")
                    self.respond(200, result)
                except (OSError, KeyError, TypeError, RuntimeError):
                    self.respond(503, {"error": "Could not check the reserve on Devnet. Try again."})
                return
            if self.path in ("/api/next-stop", "/api/two-stops"):
                fuel, road = data.get("fuel_cents"), data.get("road_cents")
                next_stop.amounts(fuel, road)
                keep_shares = data.get("keep_shares", False)
                if type(keep_shares) is not bool or (keep_shares and self.path != "/api/next-stop"):
                    raise ValueError("Choose how to handle unused shares for one fuel stop")
                gallons = data.get("gallons") if self.path == "/api/two-stops" else None
                pump_mills, oil_rise = data.get("pump_mills"), data.get("oil_rise", False)
                if self.path == "/api/two-stops":
                    next_stop.check_fuel_inputs(gallons, pump_mills, oil_rise)
                try:
                    holdback = None
                    if gallons:
                        market = self.server.offer_fuel.read() if pump_mills is None or oil_rise else fuel_update.saved()
                        holdback = next_stop.fuel_holdback(gallons, market, pump_mills=pump_mills, oil_rise=oil_rise)
                    result = next_stop.preview(self.server.public_payment.read(), self.server.public_offer.read(),
                                               fuel, road, devnet.request, holdback=holdback, keep_shares=keep_shares)
                    self.respond(200, next_stop.observed(result))
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    self.respond(503, {"error": "The market and reserve checks could not confirm this preview. Try again."})
                return
            if self.path not in (
                "/api/quote-surcharge",
                "/api/apply-surcharge",
                "/api/deposit",
                "/api/review",
                "/api/approve",
                "/api/cancel",
                "/api/check-vendor",
                "/api/verify",
                "/api/reset",
                "/api/record-payment",
                "/api/undo-record",
                "/api/return-quote",
                "/api/return-reserve",
                "/api/verify-return",
                "/api/return-receipt",
            ):
                self.respond(404, {"error": "Action unavailable"})
                return
            self.respond(
                200, self.server.settlement.act(self.path.rsplit("/", 1)[1], data)
            )
        except (TypeError, ValueError, VaultExecutionError) as exc:
            self.respond(400, {"error": str(exc)})

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser(
        description="Run the FreightOS reserve settlement demo"
    )
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.settlement = LoadSettlement()
    server.trip_credit_lock = Lock()
    server.credit_checks = {(drop, days): PublicCheck(lambda d=drop, t=days: freight_credit.capture(d, t))
                            for drop in (20, 40, 60) for days in (23, 30)}
    server.atomic_checks = {(drop, days, protect): PublicCheck(lambda d=drop, t=days, p=protect: atomic_fuel.live(d, t, protect=p))
                           for drop, days in server.credit_checks for protect in (False, True)}
    server.reserve_credit_checks = {(drop, days): PublicCheck(lambda d=drop, t=days: reserve_credit.live(d, t))
                                  for drop, days in server.credit_checks}
    server.policy_checks = {menu: PublicCheck(lambda m=menu: payday_policy.quotes(m, True)) for menu in customer_credit.MENUS}
    server.swap_checks = {net: PublicCheck(lambda n=net: fuel_swap.capture(n)[0]) for net in customer_credit.ADVANCES}
    server.customer_credit = PublicCheck(lambda: customer_credit.replay(customer_credit.capture()))
    server.partial_previews = lru_cache(maxsize=32)(lambda amount, added, sale=0, cap=0: PublicCheck(
        lambda: partial_payment.simulate(payment=amount, extra_pledge=added, sale_raw=sale, sale_cap=cap), wait_seconds=8))
    server.midtrip_payment = PublicCheck(partial_payment.simulate, wait_seconds=8)
    server.cash_exit = PublicCheck(lambda: flash_exit.simulate(cash_raw=flash_exit.SAMPLE_CASH))
    server.flash_exit = PublicCheck(flash_exit.simulate)
    server.split_payment = PublicCheck(split_payment.simulate)
    server.repeat_credit = PublicCheck(lambda: repeat_credit.verify(repeat_credit.capture()))
    server.settled_credit = PublicCheck(lambda: load_credit.settled(devnet.request))
    server.forward_check = PublicCheck(forward.ResultCheck().read)
    server.trip_books = PublicCheck(trip_cover.capture)
    server.fuel_cover = PublicCheck(fuel_cover.live)
    server.offer_fuel = PublicCheck(fuel_update.live)
    server.public_reserve = PublicCheck(reserve_observation)
    offer_run = json.loads(public_offer.SAMPLE.read_bytes())
    server.public_offer = PublicCheck(lambda: public_offer.verify(offer_run, devnet.request))
    public_run = json.loads(devnet.SAMPLE.read_bytes())
    server.public_payment = PublicCheck(lambda: devnet.verify(public_run, devnet.request))
    server.return_previews = {portion: PublicCheck(lambda portion=portion: devnet.reserve_preview(
        public_run, server.public_payment.read(), portion, devnet.request)) for portion in ("road", "all")}
    print(
        f"FreightOS at http://127.0.0.1:{server.server_port} (practice bill and public Devnet receipt)",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
