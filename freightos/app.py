import argparse
import gzip
import hashlib
import json
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, RLock
from time import monotonic
from uuid import uuid4

from freightos.capture import ROOT
from freightos.fork import ReserveFork, VaultExecutionError
from freightos import bill, reserve_return
from freightos.funding import DATA, LOAD, demo_plans, funding_plan
from hedging.quick_pay import price_calendar
from hedging.payment_timing import reserve_deadline

ASSETS = Path(__file__).with_name("web")


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
    for row in cases.values():
        case = row["case"]
        reserve = case["funding"][0]
        selected = row["decision"]
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
    return {**counts, "one_day_delay": timing}


class LoadSettlement:
    def __init__(self):
        self.lock = RLock()
        self.fork = ReserveFork()
        self.deposit_receipt = None
        self.manual_payment = None
        self.record_version = uuid4().hex
        self.quote_balances = None
        self.returns = {}
        self.plans, self.edges, self.levels, self.policies = demo_plans()

    def setup_quotes(self):
        balances = self.fork.balance(self.fork.cash), self.fork.balance(self.fork.shares)
        if balances != self.quote_balances:
            quotes = {}
            for profile in self.plans:
                try:
                    quotes[profile] = self.fork.deposit_quote(self.reserve_target(profile))
                except VaultExecutionError:
                    quotes[profile] = None
            self.quotes = quotes
            self.quote_balances = balances
        return self.quotes

    def state(self):
        with self.lock:
            return {
                "load": LOAD,
                "slot": self.fork.slot,
                "reserve_shares_raw": self.fork.balance(self.fork.shares),
                "wallet_usdc_raw": self.fork.balance(self.fork.cash),
                "setup_cents": {} if self.manual_payment or LOAD in self.fork.receipts else self.setup_quotes(),
                "deposit": self.deposit_receipt,
                "last_return": self.latest_return(),
                "manual_payment": self.manual_payment,
                "record_version": self.record_version,
                "receipt": self.fork.receipts.get(LOAD),
                "review": self.fork.pending_review(),
                "plans": self.plans["quick pay"],
                "funding_plans": self.plans,
                "pairing_values": self.edges,
                "market_levels": self.levels,
                "invoice_policies": self.policies,
            }

    def latest_return(self):
        if self.returns:
            receipt = next(reversed(self.returns.values()))
            return {k: receipt[k] for k in ("signature", "quote", "wallet_after_raw")}

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
        quote = reserve_return.return_quote(self.fork, target, {
            "profile": profile, "load": LOAD, "version": self.record_version,
            "payment_signature": settled["signature"] if settled else None})
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
                self.quote_balances = None
                self.record_version = uuid4().hex
                return {"reset": True}
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
                    if amount > cap:
                        raise ValueError("Reserve setup exceeds the approved amount")
                    if amount:
                        if self.fork.pending_review():
                            raise ValueError("Finish or cancel the pending payment review")
                        self.deposit_receipt = self.fork.deposit(amount * 10000)
                    return self.deposit_receipt or {"deposited": False}
                if not self.deposit_receipt:
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


class PublicReserve:
    def __init__(self):
        self.lock = Lock()
        self.valid_until = 0
        self.observation = None

    def read(self):
        from freightos.live import reserve_observation

        if not self.lock.acquire(blocking=False):
            raise ValueError("Public reserve check is in progress")
        try:
            if monotonic() >= self.valid_until or self.observation is None:
                self.observation = reserve_observation()
                self.valid_until = monotonic() + 30
            return self.observation
        finally:
            self.lock.release()


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
        self.end_headers()
        self.wfile.write(body)

    def respond(self, status, obj):
        self.reply(status, "application/json", json.dumps(obj, separators=(",", ":")).encode())

    def do_GET(self):
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
        if self.path == "/evidence/gemini":
            self.reply(
                200,
                "application/json",
                (ROOT / "artifacts/gemini_ladder/active.json").read_bytes(),
            )
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
            "/overview.js": ("overview.js", "text/javascript"),
            "/bill": ("index.html", "text/html; charset=utf-8"),
            "/trial": ("trial.html", "text/html; charset=utf-8"),
            "/trial.js": ("trial.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/app.js": ("app.js", "text/javascript"),
            "/wallet.js": ("wallet.js", "text/javascript"),
            "/bill_tabs.js": ("bill_tabs.js", "text/javascript"),
            "/media/freightos-mark.svg": ("media/freightos-mark.svg", "image/svg+xml"),
            "/media/instrument-serif-latin-400-italic.woff2": ("media/instrument-serif-latin-400-italic.woff2", "font/woff2"),
            "/media/barlow-condensed-latin-600-normal.woff2": ("media/barlow-condensed-latin-600-normal.woff2", "font/woff2"),
            "/media/inter-latin-wght-normal.woff2": ("media/inter-latin-wght-normal.woff2", "font/woff2"),
            "/common.js": ("common.js", "text/javascript"),
        }
        if self.path not in assets:
            self.respond(404, {"error": "Page unavailable"})
            return
        name, mime = assets[self.path]
        self.reply(200, mime, (ASSETS / name).read_bytes(), static=True)

    def do_POST(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != host or self.headers.get("Origin") not in (
            None,
            "http://" + host,
        ):
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
            if self.path not in (
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
    server.public_reserve = PublicReserve()
    print(
        f"FreightOS at http://127.0.0.1:{server.server_port} (local fork, test funds)",
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
