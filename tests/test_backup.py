from datetime import date, timedelta

from freightos import backup, trip_cover


def observations():
    end = date(2025, 4, 7)
    return [(0, 0, (end + i*backup.WEEK).isoformat()) for i in range(52)]


def test_rank_counts_ties_and_includes_finite_sample_adjustment():
    assert backup.buffer(list(range(26))) == 24
    assert backup.buffer([0]*24 + [100, 500]) == 100
    assert backup.buffer([0]*25 + [500]) == 0


def test_trailing_estimates_use_only_released_gaps_and_fixed_buffer_stays_frozen():
    samples = observations()
    gaps = [i * 100 for i in range(len(samples))]
    as_of = date.fromisoformat(samples[-1][2]) + backup.WEEK
    result = backup.estimate(samples, gaps, as_of)
    for record in result["records"]:
        cutoff = date.fromisoformat(record["week"]) - 2*backup.WEEK
        available = [v for s, v in zip(samples, gaps) if date.fromisoformat(s[2]) <= cutoff]
        assert record["recent_cents"] == sorted(available[-26:])[-2]
        assert record["through"] == cutoff.isoformat()
        assert record["fixed_cents"] == result["fixed_cents"]
    prefix = result["records"][:3]
    shock_day = prefix[-1]["week"]
    shocked = [v if s[2] < shock_day else 100000 for s, v in zip(samples, gaps)]
    after = backup.estimate(samples, shocked, as_of)
    assert [r["recent_cents"] for r in after["records"][:4]] == [r["recent_cents"] for r in result["records"][:4]]
    assert after["fixed_cents"] == result["fixed_cents"]
    future_samples = samples + [(0, 0, as_of.isoformat())]
    assert backup.estimate(future_samples, gaps + [50000], as_of) == result
    assert backup.estimate(samples, gaps, as_of + backup.WEEK)["target_cents"] is None
    assert backup.estimate(samples[-12:], gaps[-12:], as_of) is None


def test_backup_follows_cash_and_the_selected_hedge_on_recorded_trip():
    trip = dict(trip_cover.EXAMPLE, region="midwest", diesel_model="recent", pump_cash_cents=500, spend_limit_cents=300)
    target = trip_cover.analyze(trip_cover.recorded(), trip, reserve={"available_cents": 3002, "road_cents": 2500})["pump_target"]
    checks = target["backup_checks"]
    q = checks["original"]
    assert (q["target_cents"], q["fixed_cents"], len(q["records"])) == (3288, 0, 41)
    assert (q["recent"]["misses"], q["fixed"]["misses"]) == (3, 13)
    assert checks["cash_mix"]["fixed_cents"] == 68
    assert checks["delay_mix"]["recent"]["misses"] == 5
    assert target["delay_mix"]["short_training_weeks"] == 8
    assert all(c["records"][-1]["week"] == q["through"] for c in checks.values())
