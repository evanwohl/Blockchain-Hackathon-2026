from datetime import date
from fractions import Fraction

from freightos import backup, forward, fuel_volatility as volatility


def test_frozen_volatility_comparison_reconciles_with_separate_credit_evidence():
    q = forward.published_analysis()["volatility"]
    credit = forward.funding()["credit"]["loan"]["advance_cents"]
    assert q["weeks"] == 41
    assert q["scaled"] == {"misses": 1, "mean_cents": 4183, "worst_uncovered_cents": 7279}
    assert q["recent"] == {"misses": 5, "mean_cents": 2922, "worst_uncovered_cents": 7520}
    assert q["target_cents"] - credit == 89
    assert forward.funding()["targets"]["delay_mix"] - credit == 486


def test_pair_resizing_reprices_the_strike_and_preserves_deadline(monkeypatch):
    first = date(2025, 7, 7)
    days = [first + i * backup.WEEK for i in range(33)]
    fill = days[-1]
    cutoff = fill - 2 * backup.WEEK
    levels = {day - 2 * backup.WEEK: (Fraction(1), Fraction(1)) for day in days}
    levels[cutoff] = Fraction(2), Fraction(3, 2)
    monkeypatch.setattr(volatility, "scales", lambda *_: levels)
    trip = {"pump_mills": 4000, "gallons": 1, "region": "midwest", "budget_cents": 400, "pump_cash_cents": 0}
    samples = [(0, 0, d.isoformat(), Fraction(1, 2), 35, 404) for d in days]
    plan = {"legs": [{"strike": "81", "lots": 3}]}
    def run(rows, as_of):
        check = {"as_of": as_of.isoformat(), "target_cents": 4, "records": [
            {"week": fill.isoformat(), "gap_cents": 4, "recent_cents": 4, "fixed_cents": 0}]}
        return volatility.compare(check, rows, trip, plan["legs"], plan, 80, 1, (4, 3), fill)
    q = run(samples, fill - backup.WEEK)
    assert q["target_cents"] == 2  # 405.25 cents rounds to 406, less 401 in cash and 3 in oil proceeds.
    assert q["scaled"]["worst_uncovered_cents"] == 2
    changed = [s if s[2] <= cutoff.isoformat() else (*s[:3], Fraction(1000), 65535, 9999) for s in samples]
    assert run(changed, fill - backup.WEEK) == q
    assert run(samples, fill - 2 * backup.WEEK)["target_cents"] is None
    assert run(samples, fill + backup.WEEK)["target_cents"] is None
    levels[days[12] - 2 * backup.WEEK] = (1, 0)
    assert run(samples, fill - backup.WEEK) is None


def test_scales_use_only_prior_matched_observations_and_skip_stale_windows(monkeypatch):
    start = date(2025, 1, 6)
    days = [start + i * backup.WEEK for i in range(40)]
    series = [(day, Fraction(i + 1, 100), i + 1) for i, day in enumerate(days) if i not in (29, 30, 31, 32)]
    monkeypatch.setattr(volatility.fill_replay, "changes", lambda *_: series)
    scale = volatility.scales("scale-test", (4, 3))
    assert days[24] not in scale and days[31] not in scale
    mean = Fraction(sum(range(19, 27)), 16) + Fraction(sum(range(1, 27)), 52)
    assert scale[days[25]] == (mean/100, mean)
    assert scale[days[30]] == scale[days[28]]
    assert days[39] in scale
    del series[series.index(next(row for row in series if row[0] == days[33])):]
    truncated = volatility.scales("scale-test-truncated", (4, 3))
    assert truncated[days[30]] == scale[days[30]]
    assert days[31] not in truncated


def test_registered_forecast_was_fixed_before_expiry_and_scores_without_refitting():
    from datetime import datetime
    import hashlib

    record = forward.registered_backup()
    assert record["ticket_sha256"] == forward.TICKET_HASH
    assert record["prices_sha256"] == hashlib.sha256(forward.replay_prices.SNAPSHOT.read_bytes()).hexdigest()
    assert datetime.fromisoformat(record["gemini_checked"]) <= datetime.fromisoformat(record["recorded_at"]) < datetime.fromisoformat(record["expiry"])
    assert record["usual_cents"] == forward.funding()["targets"][record["plan"]]
    assert record["adjusted_cents"] == forward.published_analysis()["volatility"]["target_cents"]
    assert forward.published_score({"close": "84"})["backup_outcome"] is None
    assert forward.published_score(diesel={"mills": 6500})["backup_outcome"] is None
    for gap, winner in [(0, "adjusted"), (2901, "adjusted"), (2940, "adjusted"), (2941, "usual"), (5000, "usual")]:
        rows = forward.compare_buffers(record, gap)
        assert min(rows, key=lambda k: rows[k]["score"]) == winner
        for key, row in rows.items():
            assert record[key + "_cents"] + row["short_cents"] - row["spare_cents"] == gap
    complete = forward.published_score({"close": "84"}, {"mills": 6500})
    assert complete["backup_outcome"]["adjusted"]["short_cents"] == 4058 - 2901
