"""Freeze and score the Midwest paper fill against observed Gemini and EIA outcomes."""

import gzip
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from functools import cache
from html import unescape
from pathlib import Path
from urllib.request import Request, urlopen

from freightos import backup, devnet, diesel_model, fill_replay, freight_credit, fuel_update, fuel_volatility, next_stop, payday, public_offer, replay_prices, trip_cover
from freightos.fuel_markets import reported_close

TICKET = trip_cover.SNAPSHOT.with_name("forward_fill.json.gz")
BACKUPS = TICKET.with_name("forward_funding.json")
TICKET_HASH = "8c259e98125d87e5a4087a7db002b7bc72f862653ad0210825b12a5b6583c4c0"
EIA = "https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=EMD_EPD2D_PTE_R20_DPG&f=W"
NAMES = {"cash": "Cash only", "original": "Original trip hedge", "history_mix": "Fit for on-time payouts",
         "delay_mix": "Fit for one late payout"}


def diesel_page():
    with urlopen(Request(EIA, headers={"User-Agent": "FreightOS/0.1"}), timeout=6) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise ValueError("Unexpected EIA page size")
    return raw


def diesel_observation(raw, day, as_of):
    page = unescape(raw.decode("utf-8")).replace("\xa0", " ")
    if "Weekly Midwest No 2 Diesel Retail Prices" not in page:
        raise ValueError("Unexpected diesel series")
    releases = re.findall(r">\s*Release Date:\s*(\d+/\d+/\d+)\s*<", page)
    release, = [datetime.strptime(r, "%m/%d/%Y").date() for r in releases]
    if release > as_of:
        raise ValueError("Future EIA release")
    if release < day:
        return None
    month = day.strftime("%Y-%b")
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.I | re.S)
    matches = []
    for row in rows:
        cells = [c.strip() for c in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)]
        if cells[:1] == [month]:
            matches.extend(value for stamp, value in zip(cells[1::2], cells[2::2]) if stamp == day.strftime("%m/%d"))
    if len(matches) != 1 or not re.fullmatch(r"\d+\.\d{3}", matches[0]):
        raise ValueError("EIA has no verified price for the specified observation")
    return {"week": day.isoformat(), "mills": int(Decimal(matches[0]) * 1000), "release": release.isoformat(),
            "source_sha256": hashlib.sha256(raw).hexdigest()}


def reserve_quote():
    first = devnet.verify(json.loads(devnet.SAMPLE.read_bytes()), devnet.request)
    second = public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes()), devnet.request)
    return next_stop.reserve_budget(first, second, devnet.request)


def freeze():
    with ThreadPoolExecutor(max_workers=3) as pool:
        books = pool.submit(trip_cover.capture)
        redemption = pool.submit(reserve_quote)
        page = diesel_page()
        snapshot, vault = books.result(), redemption.result()
    fuel = fuel_update.compare(snapshot["market"])
    expiry = datetime.fromisoformat(fuel["expiry"])
    fill_day = expiry.date() + timedelta(days=7-expiry.weekday())
    baseline = diesel_observation(page, fill_day - timedelta(days=7), datetime.now(timezone.utc).date())
    if not baseline or fuel["status"] != "active":
        raise ValueError("The paper fill requires a published starting price and open event")
    trip = dict(trip_cover.EXAMPLE, region="midwest", gallons=100, pump_mills=baseline["mills"],
                budget_cents=baseline["mills"]*10, spend_limit_cents=300, pump_cash_cents=500, diesel_model="recent")
    reserve = {"slot": vault["slot"], "available_cents": vault["return_raw"] // vault["raw_units_per_cent"], "road_cents": 2500}
    target = trip_cover.analyze(snapshot, trip, reserve=reserve)["pump_target"]
    plans = {"cash": {"upfront_cents": 0, "legs": []}, "original": target["plan"]}
    for key in ("history_mix", "delay_mix"):
        if key in target:
            plans[key] = target[key]["plan"]
    now = datetime.now(timezone.utc)
    if not datetime.fromisoformat(snapshot["checked_at"]) <= now < expiry:
        raise ValueError("The comparison must be fixed before expiry")
    return {"version": 1, "recorded_at": now.isoformat(), "event": fuel["target_ticker"], "expiry": fuel["expiry"],
            "fill_day": fill_day.isoformat(), "baseline": baseline, "eia": EIA, "trip": trip, "reserve": reserve,
            "vault": vault, "quotes": snapshot, "plans": plans, "primary": "delay_mix" if "delay_mix" in plans else "original",
            "model_sha256": hashlib.sha256(diesel_model.ARTIFACT.read_bytes()).hexdigest()}


def write_ticket(path):
    ticket = freeze()
    raw = json.dumps(ticket, sort_keys=True, separators=(",", ":")).encode()
    with path.open("xb") as handle:
        handle.write(gzip.compress(raw, mtime=0))


@cache
def saved():
    raw = gzip.decompress(TICKET.read_bytes())
    if hashlib.sha256(raw).hexdigest() != TICKET_HASH:
        raise ValueError("The published forward ticket changed")
    return json.loads(raw)


def freeze_funding(ticket):
    target = trip_cover.analyze(ticket["quotes"], ticket["trip"], reserve=ticket["reserve"])["pump_target"]
    targets = {}
    for key, fixed in ticket["plans"].items():
        mix = {"original": target, "cash": target.get("cash_mix", target)}.get(key, target.get(key))
        if mix["plan"]["legs"] != fixed["legs"] or mix["plan"]["upfront_cents"] != fixed["upfront_cents"]:
            raise ValueError("Rebuilt plan differs from frozen positions")
        name = key if fixed["legs"] else "cash_mix"
        targets[key] = target["backup_checks"][name]["target_cents"]
    amount = targets[ticket["primary"]]
    loan = freight_credit.trip_draw(amount, 1000, 40, 23) if amount else None
    now = datetime.now(timezone.utc)
    if not datetime.fromisoformat(ticket["recorded_at"]) <= now < datetime.fromisoformat(ticket["expiry"]):
        raise ValueError("Funding must be recorded before the outcome")
    return {"ticket_sha256": TICKET_HASH, "recorded_at": now.isoformat(), "targets": targets, "credit": loan,
            "pledge_limit_millis": 1000, "stress_drop_pct": 40, "days": 23}


@cache
def funding():
    raw = BACKUPS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != "e17d260cd9203321f4c627334c508c6dad39f7a628c4328ce6765d98f06ddf10":
        raise ValueError("Published credit evidence changed")
    result = json.loads(raw)
    recorded = datetime.fromisoformat(result["recorded_at"])
    if (result["ticket_sha256"] != TICKET_HASH or
            not datetime.fromisoformat(saved()["recorded_at"]) <= recorded < datetime.fromisoformat(saved()["expiry"])):
        raise ValueError("Funding evidence differs from the frozen ticket")
    return result


def settlement(ticket, observation, now):
    stamp = datetime.fromisoformat(observation["reported_at"])
    event = observation["event"]
    original = ticket["quotes"]["market"]["events"][ticket["event"]]["event"]
    if (stamp < datetime.fromisoformat(ticket["quotes"]["market"]["events"][ticket["event"]]["reported_at"])
            or stamp > now + timedelta(seconds=2)
            or any(event[key] != original[key] for key in ("id", "ticker", "series", "sourceDetails", "expiryDate"))
            or not fuel_update.instruments(original).issubset(fuel_update.instruments(event))):
        raise ValueError("Oil event does not match the frozen ticket")
    if event["status"] != "settled":
        if event["status"] in ("active", "closed"):
            return None
        raise ValueError("Unrecognized Gemini outcome")
    close, _ = reported_close(event, stamp)
    return {"close": str(close), "resolved_at": event["resolvedAt"]}


def cash_edge(plan, funds, gallons):
    premium = plan["upfront_cents"]
    payout = 0
    first = None
    for leg in sorted(plan["legs"], key=lambda row: Decimal(row["strike"])):
        payout += leg["lots"]
        if first is None and payout > premium:
            first = leg["strike"]
    if first is not None:
        return {"minimum_strike": first, "minimum_pump_mills": (funds + premium)*10 // gallons + 1,
                "maximum_saving_cents": payout - premium}


@cache
def published_analysis():
    ticket = saved()
    trip = ticket["trip"]
    plan = ticket["plans"][ticket["primary"]]
    settled, = [item for item in ticket["quotes"]["market"]["events"].values() if item["event"]["status"] == "settled"]
    close, _ = reported_close(settled["event"], datetime.fromisoformat(settled["reported_at"]))
    offsets = fill_replay.expiry_offsets(datetime.fromisoformat(settled["event"]["expiryDate"]).date(),
                                         datetime.fromisoformat(ticket["expiry"]).date())
    offers = sorted(plan["legs"], key=lambda leg: Decimal(leg["strike"]))
    fuel_cash = max(0, ticket["reserve"]["available_cents"] - ticket["reserve"]["road_cents"])
    as_of = datetime.fromisoformat(ticket["quotes"]["checked_at"]).date()
    _, later = fill_replay.fills(trip, Fraction(close), offers, fuel_cash, as_of, offsets)
    fill_day = date.fromisoformat(ticket["fill_day"])
    gaps, _ = fill_replay.pump_gaps(later, offers, plan)
    check = backup.estimate(later, gaps, as_of)
    return {"payday": payday.estimate(later, trip, fuel_cash, offers, plan, as_of, funding()["days"]),
            "pump_value": trip_cover.pump_price_check(later, offers, plan, trip, fill_day, as_of),
            "volatility": fuel_volatility.compare(check, later, trip, offers, plan, Fraction(close), fuel_cash, offsets, fill_day)}


def published_value():
    return published_analysis()["pump_value"]


def score(ticket, funding_check, oil=None, diesel=None):
    trip = ticket["trip"]
    funds = trip["budget_cents"] + trip["pump_cash_cents"] + max(0, ticket["reserve"]["available_cents"] - ticket["reserve"]["road_cents"])
    bill = (diesel["mills"]*trip["gallons"] + 9)//10 if diesel else None
    credit = funding_check["credit"]
    capacity = credit["loan"]["advance_cents"] if credit and credit["loan"] else 0
    plans = []
    for key in NAMES:
        if key not in ticket["plans"]:
            continue
        plan = ticket["plans"][key]
        premium = plan["upfront_cents"]
        payout = 0 if not plan["legs"] else None
        if oil:
            close = Decimal(oil["close"])
            payout = sum(leg["lots"] for leg in plan["legs"] if close >= Decimal(leg["strike"]))
        gap = max(0, bill - funds - payout) if bill is not None and payout is not None else None
        plans.append({"key": key, "name": NAMES[key], "premium_cents": premium, "payout_cents": payout,
                      "short_cents": gap, "extra_cash_cents": premium + gap if gap is not None else None,
                      "backup_cents": funding_check["targets"][key],
                      "after_credit_cents": max(0, gap-capacity) if key == ticket["primary"] and gap is not None else None})
    return {"recorded_at": ticket["recorded_at"], "expiry": ticket["expiry"], "fill_day": ticket["fill_day"],
            "credit": credit, "trip": trip, "baseline": ticket["baseline"], "event": ticket["event"], "primary": ticket["primary"], "positions": ticket["plans"][ticket["primary"]], "reserve": ticket["reserve"],
            "edge": cash_edge(ticket["plans"][ticket["primary"]], funds, trip["gallons"]),
            "funds_cents": funds, "bill_cents": bill, "oil": oil, "diesel": diesel, "plans": plans}


def record_backup(path):
    ticket = saved()
    event = fuel_update.fetch_event(ticket["event"])
    raw = diesel_page()
    now = datetime.now(timezone.utc)
    fill_day = date.fromisoformat(ticket["fill_day"])
    if (not datetime.fromisoformat(ticket["recorded_at"]) <= now < datetime.fromisoformat(ticket["expiry"])
            or abs((now - datetime.fromisoformat(event["reported_at"])).total_seconds()) > 120
            or settlement(ticket, event, now) is not None or event["event"]["status"] != "active"
            or diesel_observation(raw, fill_day, now.date()) is not None):
        raise ValueError("Both fill outcomes must still be pending to register a backup forecast")
    baseline = diesel_observation(raw, fill_day - timedelta(days=7), now.date())
    if not baseline or baseline["mills"] != ticket["baseline"]["mills"]:
        raise ValueError("The starting diesel observation has changed")
    primary = ticket["primary"]
    volatility = published_analysis()["volatility"]
    if not volatility or volatility["target_cents"] is None:
        raise ValueError("No current price-adjusted forecast")
    record = {"version": 1, "recorded_at": now.isoformat(), "ticket_sha256": TICKET_HASH, "plan": primary,
              "gemini_checked": event["reported_at"], "event": ticket["event"], "expiry": ticket["expiry"],
              "fill_day": ticket["fill_day"], "last_diesel": baseline,
              "prices_sha256": hashlib.sha256(replay_prices.SNAPSHOT.read_bytes()).hexdigest(),
              "usual_cents": funding()["targets"][primary], "adjusted_cents": volatility["target_cents"],
              "short_weight": 9, "surplus_weight": 1}
    with path.open("x") as output:
        json.dump(record, output, indent=2)
        output.write("\n")


@cache
def registered_backup():
    raw = TICKET.with_name("forward_backup.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != "932ff8d993939a09c5534d06e26e1f332f39892d78ef0fdd34b526c138f90135":
        raise ValueError("The published backup forecast changed")
    return json.loads(raw)


def compare_buffers(record, gap):
    if gap is None:
        return None
    rows = {}
    for name in ("usual", "adjusted"):
        difference = gap - record[name + "_cents"]
        short, spare = max(0, difference), max(0, -difference)
        rows[name] = {"short_cents": short, "spare_cents": spare,
                      "score": short * record["short_weight"] + spare * record["surplus_weight"]}
    return rows


def published_score(oil=None, diesel=None):
    result = score(saved(), funding(), oil, diesel)
    record = registered_backup()
    selected, = [row for row in result["plans"] if row["key"] == record["plan"]]
    return {**result, **published_analysis(), "backup_forecast": record,
            "backup_outcome": compare_buffers(record, selected["short_cents"])}


def live(previous=None):
    ticket = saved()
    now = datetime.now(timezone.utc)
    errors = []
    oil = previous.get("oil") if previous else None
    diesel = previous.get("diesel") if previous else None
    try:
        if oil is None:
            observation = fuel_update.fetch_event(ticket["event"])
            oil = settlement(ticket, observation, datetime.now(timezone.utc))
    except (OSError, ValueError, TypeError, KeyError, ArithmeticError):
        errors.append("Gemini")
    try:
        observation = diesel_observation(diesel_page(), date.fromisoformat(ticket["fill_day"]), now.date())
        if observation and (not diesel or observation["release"] >= diesel["release"]):
            diesel = observation
    except (OSError, ValueError, TypeError, KeyError):
        errors.append("EIA")
    return {**published_score(oil, diesel), "errors": errors, "checked_at": datetime.now(timezone.utc).isoformat()}


class ResultCheck:
    def __init__(self):
        self.previous = None

    def read(self):
        self.previous = live(self.previous)
        return self.previous


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--freeze", type=Path)
    group.add_argument("--record-backup", type=Path)
    args = parser.parse_args()
    if args.freeze:
        write_ticket(args.freeze)
    elif args.record_backup:
        record_backup(args.record_backup)
    else:
        print(json.dumps(live(), indent=2))
