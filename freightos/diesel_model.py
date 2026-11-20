"""Regional one-week diesel budgets conditional on the trip's oil scenario."""

import gzip
import hashlib
import json
from datetime import date, timedelta
from fractions import Fraction
from functools import cache
from math import ceil, exp, log, sqrt
from statistics import median

from freightos import regional_diesel

ARTIFACT = regional_diesel.SNAPSHOT.with_name("diesel_model.json")
AUDIT = ARTIFACT.with_suffix(".audit.json.gz")
WINDOW = 52


class CalibrationUnavailable(ValueError):
    pass


def quantile_fit(design, target, intercepts, priors, limits):
    import numpy as np
    from scipy.optimize import linprog
    from scipy.sparse import bmat, csr_matrix, eye

    design = np.asarray(design, dtype=float)
    n, columns = design.shape
    k = len(priors)
    penalties = .05 * np.abs(design[:, intercepts:]).sum(axis=0)
    slope_rows = np.eye(k, columns, k=intercepts)
    matrix = bmat([[csr_matrix(design), eye(n), -eye(n), None, None],
                   [csr_matrix(slope_rows), None, None, -eye(k), eye(k)]], format="csr")
    objective = np.r_[np.zeros(columns), .9*np.ones(n), .1*np.ones(n), penalties, penalties]
    bounds = [(0, None)]*intercepts + [limit if penalty else (prior, prior)
                                       for limit, penalty, prior in zip(limits, penalties, priors)]
    result = linprog(objective, A_eq=matrix, b_eq=np.r_[target, priors],
                     bounds=bounds + [(0, None)]*(2*n + 2*k), method="highs")
    if not result.success:
        raise ValueError("Could not fit the regional price response")
    return result.x[:columns]


def curve(base, up, down):
    return {"base_cents": max(0, ceil(round(float(base), 8))), "up_millis": round(1000*up), "down_millis": round(1000*down)}


def fitted_curves(weeks, lag):
    regions = list(regional_diesel.REGIONS)
    pooled_x, pooled_y, curves = [], [], {}
    for index, region in enumerate(regions):
        local_x, local_y = [], []
        identity = [int(index == j) for j in range(len(regions))]
        for end, oil, prices in weeks:
            rising, falling = max(0, oil), min(0, oil)
            common, local = lag.get(end, {}).get(region, (0, 0))
            pooled_x.append([*identity, rising, falling, common, local])
            local_x.append([1, rising, falling])
            local_y.append(float(prices[region]))
        pooled_y.extend(local_y)
        curves[region] = curve(*quantile_fit(local_x, local_y, 1, [1, 1], [(0, 2)]*2))
    pooled = quantile_fit(pooled_x, pooled_y, len(regions), [1, 1, 0, 0], [(0, 2)]*2 + [(-1, 1)]*2)
    return curves, pooled


def change(curve, oil_dollars):
    sensitivity = curve["up_millis"] if oil_dollars >= 0 else curve["down_millis"]
    return curve["base_cents"] + oil_dollars * Fraction(sensitivity, 420)


def score(errors):
    loss = sum(max(9*e, -e) for _, e in errors) / (10*len(errors))
    worst_day, worst = max(errors, key=lambda row: row[1])
    return {"misses": sum(e > 0 for _, e in errors), "score": round(float(loss), 3),
            "worst_week": worst_day, "worst_miss_cents": max(0, ceil(worst)),
            "four_fills": regional_diesel.consecutive_fill_stress(errors, 0)}


def paired_gain(fixed, learned):
    days = {}
    for (day, a), (other, b) in zip(fixed, learned, strict=True):
        if day != other:
            raise ValueError("Forecast dates differ")
        days[date.fromisoformat(day)] = float((max(9*a, -a) - max(9*b, -b)) / 10)
    n = len(days)
    mean = sum(days.values()) / n
    centered = {day: v-mean for day, v in days.items()}
    variance = sum(v*v for v in centered.values())
    for lag in range(1, 5):
        cross = sum(v * centered.get(day - timedelta(weeks=lag), 0) for day, v in centered.items())
        variance += 2 * (1-lag/5) * cross
    width = 1.645 * sqrt(max(0, variance)) / n
    return {"mean": round(mean, 3), "low": round(mean-width, 3), "high": round(mean+width, 3)}


def weights(records):
    if not records:
        return [1/3]*3
    eta = sqrt(8*log(3)/26)
    totals = [sum(column) for column in zip(*records[-26:])]
    raw = [exp(-eta * (v - min(totals))) for v in totals]
    return [w/sum(raw) for w in raw]


def blend(curves, allocation):
    return {key: round(sum(w*c[key] for w, c in zip(allocation, curves))) for key in curves[0]}


def train(rows, audit):
    regions = list(regional_diesel.REGIONS)
    weeks, lag = [], {}
    for a, b in zip(rows, rows[1:]):
        end = date.fromisoformat(b["date"])
        if end - date.fromisoformat(a["date"]) != timedelta(days=7):
            continue
        oil = Fraction(b["oil_cents"] - a["oil_cents"], 42)
        prices = {r: Fraction(b["diesel_mills"][r] - a["diesel_mills"][r], 10) for r in regions}
        weeks.append((end, oil, prices))
        deviations = {r: p-oil for r, p in prices.items()}
        common = median(deviations.values())
        lag[end + timedelta(days=14)] = {r: (common, deviations[r] - common) for r in regions}

    split = date.fromisoformat(regional_diesel.TEST_START)
    standard = {}
    for r in regions:
        past = sorted(prices[r]-x for end, x, prices in weeks if end < split)
        standard[r] = curve(past[ceil(Fraction(9*len(past), 10))-1], 1, 1)
    methods = ("fixed", "regional_only", "pooled", "recent")
    errors = {r: {method: [] for method in methods} for r in regions}
    regrets = {r: [] for r in regions}
    records = {}
    audit.update({r: [] for r in regions})
    last = weeks[-1][0]
    release = last + timedelta(days=7)
    projection = last + timedelta(days=14)
    for end, oil, prices in [*weeks, (projection, None, None)]:
        if end - timedelta(days=7) < split:
            continue
        cutoff = end - timedelta(days=14)
        known = [w for w in weeks if w[0] <= cutoff]
        if len(known) < WINDOW:
            continue
        regional_curves, pooled = fitted_curves(known[-WINDOW:], lag)
        up, down, lag_common, lag_local = pooled[-4:]
        for i, r in enumerate(regions):
            common, local = lag.get(end, {}).get(r, (0, 0))
            pooled_curve = curve(pooled[i] + lag_common*float(common) + lag_local*float(local), up, down)
            candidates = [standard[r], regional_curves[r], pooled_curve]
            allocation = weights([losses for day, losses in regrets[r] if day <= cutoff])
            prediction = blend(candidates, allocation)
            if prices is None:
                if not errors[r]["recent"]:
                    raise ValueError("Insufficient evaluation weeks for regional calibration")
                records[r] = {"curve": prediction, "through": last.isoformat(), "available_from": release.isoformat(),
                              "weeks": len(errors[r]["recent"]), "weights": dict(zip(methods[:3], allocation)),
                              "gain": paired_gain(errors[r]["fixed"], errors[r]["recent"]),
                              **{m: score(errors[r][m]) for m in methods}}
                continue
            forecasts = [change(c, oil * Fraction(42, 100)) for c in [*candidates, prediction]]
            audit[r].append({"end": end.isoformat(), "trained_through": known[-1][0].isoformat(),
                             "oil_move_dollars": str(oil * Fraction(42, 100)), "diesel_move_cents": str(prices[r]),
                             "forecast_cents": {m: str(f) for m, f in zip(methods, forecasts)}})
            losses = []
            for method, pred in zip(methods, forecasts):
                e = prices[r] - pred
                errors[r][method].append((end.isoformat(), e))
                losses.append(float(max(9*e, -e)/10))
            span = max(1, float(max(forecasts[:3]) - min(forecasts[:3])))
            regrets[r].append((end, [(v-min(losses[:3]))/span for v in losses[:3]]))
    if len(records) != len(regions):
        raise ValueError("Insufficient weekly history for regional calibration")
    return records


def build(audit):
    tape = regional_diesel.SNAPSHOT.read_bytes()
    rows = json.loads(gzip.decompress(tape))["prices"]
    return {"version": 2, "source_sha256": hashlib.sha256(tape).hexdigest(), "window": WINDOW, "regions": train(rows, audit)}


@cache
def calibration():
    data = json.loads(ARTIFACT.read_bytes())
    if data["version"] != 2 or data["source_sha256"] != hashlib.sha256(regional_diesel.SNAPSHOT.read_bytes()).hexdigest():
        raise ValueError("Regional model and price history do not match")
    return data


def lookup(region, as_of):
    try:
        fitted = calibration()["regions"][region]
        release = date.fromisoformat(fitted["available_from"])
        if release <= as_of < release + timedelta(days=7):
            return fitted
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return None


if __name__ == "__main__":
    ledger = {}
    artifact = build(ledger)
    audit = {"source_sha256": artifact["source_sha256"], "observations": ledger}
    content = gzip.compress(json.dumps(audit, separators=(",", ":")).encode(), mtime=0)
    artifact["audit_sha256"] = hashlib.sha256(content).hexdigest()
    AUDIT.write_bytes(content)
    ARTIFACT.write_text(json.dumps(artifact, indent=2) + "\n")
