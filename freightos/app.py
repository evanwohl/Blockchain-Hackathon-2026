import argparse
import json
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock

from freightos.capture import ROOT
from freightos.fork import ReserveFork, VaultExecutionError
from hedging.quick_pay import price_calendar

LOAD = "FOS-2048"
ASSETS = Path(__file__).with_name("web")
DATA = ROOT / "artifacts/quick_pay"
PROFILES = ("quick pay", "credit line", "cash on hand")


@cache
def trial_data():
    report = json.loads((DATA / "carrier_trial.json").read_bytes())
    cases = {row["load"]: row for row in report.pop("rows")}
    report["loads"] = [
        {"load": load, "used_reserve": bool(row["fork_receipt"])}
        for load, row in cases.items()
    ]
    return report, cases


def funding_plan(sample, evidence, scenario, profile):
    if scenario not in ("fuel budget", "oil stress"):
        raise ValueError("Unknown fuel scenario")
    bill = sample["bills"][0]
    extra = evidence["gemini"]["extra_fuel_cents_interval"][1]
    amount = bill["cents"] if scenario == "oil stress" else bill["cents"] - extra
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
    decision = price_calendar({**case, "funding": [reserve, *alternatives]})
    fallback = price_calendar({**case, "funding": alternatives})
    draw = sum(
        d["net_proceeds_cents"]
        for d in decision["funding"]
        if d["source"] == reserve["id"]
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
        "gemini": evidence["gemini"],
        "input_sha256": evidence["input_sha256"],
        "calendar": {**case, "funding": [reserve, *alternatives]},
    }


class LoadSettlement:
    def __init__(self):
        self.lock = RLock()
        self.fork = ReserveFork()
        self.deposit_receipt = None
        self.evidence = json.loads((DATA / "case_report.json").read_bytes())
        self.sample = json.loads((DATA / "sample_calendar.json").read_bytes())
        self.plans = {
            profile: {
                scenario: funding_plan(self.sample, self.evidence, scenario, profile)
                for scenario in ("fuel budget", "oil stress")
            }
            for profile in PROFILES
        }

    def state(self):
        with self.lock:
            return {
                "load": LOAD,
                "slot": self.fork.slot,
                "reserve_shares_raw": self.fork.balance(self.fork.shares),
                "wallet_usdc_raw": self.fork.balance(self.fork.cash),
                "deposit": self.deposit_receipt,
                "receipt": self.fork.receipts.get(LOAD),
                "review": self.fork.pending_review(),
                "plans": self.plans["quick pay"],
                "funding_plans": self.plans,
            }

    def act(self, action, data):
        with self.lock:
            if action == "reset":
                if LOAD not in self.fork.receipts:
                    raise ValueError("Finish the demo before starting a new session")
                self.fork = ReserveFork()
                self.deposit_receipt = None
                return {"reset": True}
            if action == "cancel":
                return self.fork.cancel(data.get("approval"))
            if action == "check-vendor":
                return self.fork.check_vendor(data.get("approval"))
            if action == "deposit":
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


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def respond(self, status, obj):
        self.reply(status, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        if self.path == "/api/trial":
            self.respond(200, trial_data()[0])
            return
        if self.path.startswith("/api/trial/"):
            row = trial_data()[1].get(self.path.removeprefix("/api/trial/"))
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
        if self.path == "/api/state":
            self.respond(200, self.server.settlement.state())
            return
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/trial": ("trial.html", "text/html; charset=utf-8"),
            "/trial.js": ("trial.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/app.js": ("app.js", "text/javascript"),
            "/media/tractor.webp": ("media/tractor.webp", "image/webp"),
            "/media/inter-latin-wght-normal.woff2": ("media/inter-latin-wght-normal.woff2", "font/woff2"),
            "/common.js": ("common.js", "text/javascript"),
        }
        if self.path not in assets:
            self.respond(404, {"error": "Page unavailable"})
            return
        name, mime = assets[self.path]
        self.reply(200, mime, (ASSETS / name).read_bytes())

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
