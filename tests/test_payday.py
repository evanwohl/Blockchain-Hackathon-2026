import gzip
import json
from copy import deepcopy
from datetime import date, timedelta

import pytest

from freightos import forward, freight_credit as credit, payday

WEEK = timedelta(weeks=1)


def test_prefix_deficit_carries_spare_cash_and_counts_payout_and_reserve_once():
    start = date(2026, 2, 2)
    levels = [4000, 4400, 4500, 3200, 3500]
    prices = {(start + i*WEEK).isoformat(): p for i, p in enumerate(levels)}
    samples = [(1, 0, (start + WEEK).isoformat())]
    trip = {"gallons": 10, "pump_mills": 4000, "budget_cents": 4000, "pump_cash_cents": 0}
    end = start + 4*WEEK
    assert payday.paths(samples, trip, prices, 150, [0, 100], 4, end) == [(start+WEEK, end, [150, 650, 0, 0])]
    assert not payday.paths(samples, trip, prices, 150, [0, 100], 4, end - WEEK)
    del prices[(start + 3*WEEK).isoformat()]
    assert not payday.paths(samples, trip, prices, 150, [0, 100], 4, end)


def test_frozen_trip_schedules_cover_the_selected_rank_at_every_stop():
    q = forward.published_analysis()["payday"]
    assert q["target_cents"] == 21548 and q["multi"]["misses"] == 1 and q["single"]["misses"] == 5
    assert len(q["records"]) == 10
    for earlier, later in zip(q["records"], q["records"][1:]):
        assert earlier["last"] < later["first"]
    for row in q["records"]:
        assert date.fromisoformat(row["trained_through"]) + 2*WEEK <= date.fromisoformat(row["first"])
    for credit_cents, expected in ((0, 5724), (2812, 4772), (21548, 0)):
        paths = [row["need_cents"] for row in q["recent"]]
        def covered(weekly):
            return sum(all(gap <= credit_cents + (i+1)*weekly for i, gap in enumerate(path)) for path in paths)
        assert covered(expected) >= 25
        if expected:
            assert covered(expected - 1) < 25


@pytest.mark.parametrize(("days", "gap", "advance"), [(23, 21508, 2781), (30, 26908, 2777)])
def test_saved_shared_draw_retains_the_stablecoins_and_binds_the_route(days, gap, advance):
    result = credit.trip_draw(gap, 1000, 40, days, destination="wallet", recorded=True)
    loan = result["loan"]
    assert loan["advance_cents"] == advance and loan["wallet_gain_raw"] == loan["advance_raw"] == advance*100
    assert loan["destination"] == "wallet" and result["remaining_cents"] + advance == gap
    assert loan["repay_cents"] == advance + loan["cost_cents"] and loan["pledge_millis"] <= 1000
    raw = json.loads(gzip.decompress((credit.DATA / "payday_credit.json.gz").read_bytes()))
    run = raw[f"{gap}-1000-40-{days}"]["run"]
    corrupted = deepcopy(run)
    corrupted["wire"] = credit.saved(40, days).get("wire", "")
    with pytest.raises(ValueError, match="transaction"):
        credit.evaluate(corrupted, 40, days, advance*100, destination="wallet")
    corrupted = deepcopy(run)
    balances = corrupted["response"]["value"]["postTokenBalances"]
    supply = next(r for r in balances if r["mint"] == str(credit.devnet.MINT) and r["owner"] != str(credit.OWNER) and int(r["uiTokenAmount"]["amount"]) > 1000000)
    supply["uiTokenAmount"]["amount"] = str(int(supply["uiTokenAmount"]["amount"]) + 1)
    with pytest.raises(ValueError, match="do not reconcile"):
        credit.evaluate(corrupted, 40, days, advance*100, destination="wallet")
    with pytest.raises(ValueError, match="No saved check"):
        credit.trip_draw(gap, 1000, 40, days, recorded=True)
