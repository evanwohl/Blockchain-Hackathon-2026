from datetime import date, timedelta
from fractions import Fraction
import hashlib
import gzip
import json

import pytest

from freightos import diesel_model as dm
from freightos import trip_cover as tc


def test_bundled_release_matches_walk_forward_fit_and_respects_publication_lag(monkeypatch):
    rows = json.loads(gzip.decompress(dm.regional_diesel.SNAPSHOT.read_bytes()))["prices"]
    ends = [date.fromisoformat(b["date"]) for a, b in zip(rows, rows[1:])
            if (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days == 7]
    scored = [end for end in ends if (end - timedelta(days=7)).isoformat() >= dm.regional_diesel.TEST_START]
    windows = []
    original = dm.fitted_curves

    def inspect(history, lag):
        windows.append([row[0] for row in history])
        return original(history, lag)

    monkeypatch.setattr(dm, "fitted_curves", inspect)
    audit = {}
    assert dm.train(rows, audit) == dm.calibration()["regions"]
    artifact = dm.AUDIT.read_bytes()
    assert hashlib.sha256(artifact).hexdigest() == dm.calibration()["audit_sha256"]
    assert audit == json.loads(gzip.decompress(artifact))["observations"]
    assert len(windows) == len(scored) + 1
    for window, end in zip(windows, scored):
        assert window == [d for d in ends if d <= end - timedelta(days=14)][-52:]
    for region, ledger in audit.items():
        scores = dm.calibration()["regions"][region]
        for method in ("fixed", "regional_only", "pooled", "recent"):
            errors = [(r["end"], Fraction(r["diesel_move_cents"]) - Fraction(r["forecast_cents"][method])) for r in ledger]
            assert dm.score(errors) == scores[method]


def test_curve_units_monotonicity_and_score():
    curve = {"base_cents": 11, "up_millis": 1250, "down_millis": 400}
    assert [dm.change(curve, x) for x in (-42, 0, 42)] == [-29, 11, 136]
    score = dm.score([("2026-01-05", Fraction(-10)), ("2026-01-12", Fraction(10))])
    assert (score["score"], score["misses"], score["worst_miss_cents"]) == (5, 1, 10)


def test_recent_estimate_moves_hedge_and_credit_without_refitting(monkeypatch):
    monkeypatch.setattr(dm, "fitted_curves", lambda *_: pytest.fail("Training on the request path"))
    trip = {**tc.EXAMPLE, "region": "midwest", "pump_cash_cents": 500, "spend_limit_cents": 300}
    reserve = {"available_cents": 3002, "road_cents": 2500}
    old = tc.analyze(tc.recorded(), trip, reserve=reserve)
    new = tc.analyze(tc.recorded(), {**trip, "diesel_model": "recent"}, reserve=reserve)
    comparison = new["regional"]["comparison"]
    assert (comparison["standard_bill_cents"], comparison["recent_bill_cents"]) == (43432, new["pump_bill_cents"] + new["cash_only_cents"] + 10)
    assert old["pump_target"]["after"]["short_cents"] == 292 and new["pump_target"]["after"]["short_cents"] > 292
    option = new["pump_target"]["floor_option"]
    assert 0 < option["short_cents"] < new["pump_target"]["after"]["short_cents"] and option["below_strike"] == "81"
    adjusted = {**trip, "diesel_model": "recent", "spend_limit_cents": option["premium_cents"]}
    for delta in (0, -1):
        adjusted["spend_limit_cents"] = option["premium_cents"] + delta
        plan = tc.analyze(tc.recorded(), adjusted, reserve=reserve)["pump_target"]
        assert (plan["after"]["short_cents"] == option["short_cents"]) == (delta == 0)


def test_model_availability_and_region_validation():
    assert dm.lookup("midwest", date(2026, 11, 15)) is None
    assert dm.lookup("midwest", date(2026, 11, 23)) is None
    assert dm.lookup("midwest", date(2026, 11, 16))["through"] == "2026-11-09"
    for invalid in ({"diesel_model": "recent"}, {"region": "west", "diesel_model": "unknown"}):
        with pytest.raises(ValueError):
            tc.validate({**tc.EXAMPLE, **invalid})

def test_weights_use_only_the_scoring_window_and_blend_stays_monotone():
    losses = [[0.1, 0.7, 0]] * 26
    w = dm.weights(losses)
    assert w == dm.weights([[0, 0, 1]] + losses)
    assert sum(w) == pytest.approx(1) and w[2] > w[0] > w[1]
    for model in dm.calibration()["regions"].values():
        q = model["curve"]
        estimates = [dm.change(q, x) for x in (-50, -2, 0, 2, 50)]
        assert estimates == sorted(estimates)
        assert model["gain"]["low"] <= model["gain"]["mean"] <= model["gain"]["high"]
