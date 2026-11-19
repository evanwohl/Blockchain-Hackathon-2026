import gzip
import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from freightos import fuel_update


def observation():
    return json.loads(gzip.decompress(fuel_update.SNAPSHOT.read_bytes()))


def test_saved_followup_reconciles_without_changing_the_signed_bill():
    data = fuel_update.saved()
    assert data["headroom_cents"] == data["paid_cents"] - data["marked_cents"] == 3866
    assert data["marked_cents"] - data["customer_cents"] == data["reserve_needed_cents"] == 53134
    assert data["close_ticker"] == "WTI2611192200"
    assert data["target_ticker"] == "WTI2611202200"
    assert data["yes_ask"] == "0.02"
    assert data["recorded_ask"] is None


@pytest.mark.parametrize(("close", "cents"), [("90.89", 74496), ("90.90", 74501), ("78", 68051)])
def test_final_mark_uses_paid_budget_and_suppresses_expired_quotes(close, cents):
    raw = observation()
    sample = raw["events"]["WTI2611202200"]
    event = sample["event"]
    end = "2026-11-20T22:01:00+00:00"
    raw["checked_at"] = sample["reported_at"] = end
    event.update(status="settled", resolvedAt=end, settlement={"value": close})
    for c in event["contracts"]:
        side = "yes" if Decimal(close) >= Decimal(c["strike"]["value"]) else "no"
        c.update(status="settled", resolvedAt=end, resolutionSide=side, settlementValue=close)
    result = fuel_update.compare(raw)
    assert result["marked_cents"] == cents
    assert result["target_outcome"] == ("no" if close == "78" else "yes")
    assert result["first_uncovered_oil"] == "90.90"
    assert result["yes_ask"] is None


def test_stale_quote_and_pending_expiry_do_not_hide_last_settlement():
    raw = observation()
    for when in ("2026-11-19T22:55:00Z", "2026-11-20T22:00:01Z"):
        raw["checked_at"] = when
        result = fuel_update.compare(raw)
        assert result["marked_cents"] == 70634
        assert result["yes_ask"] is None


@pytest.mark.parametrize("change", ["missing", "index", "symbol", "early", "contradiction"])
def test_invalid_market_observations_fail_closed(change):
    raw = observation()
    source = raw["events"]["WTI2611192200"]
    e = source["event"]
    if change == "missing":
        raw["events"].pop("WTI2611202200")
    elif change == "index":
        e["sourceDetails"]["index"] = "unrelated"
    elif change == "symbol":
        e["contracts"] = [c for c in e["contracts"] if c["strike"]["value"] != "81"]
    elif change == "early":
        source["reported_at"] = "2026-11-19T21:59:00Z"
    else:
        e["contracts"][0]["settlementValue"] = "1"
    with pytest.raises(ValueError):
        fuel_update.compare(raw)


@pytest.mark.parametrize("skew", [1, 3])
def test_gemini_response_date_can_lead_the_client_clock_by_one_second(skew):
    raw = observation()
    target = raw["events"]["WTI2611202200"]
    checked = datetime.fromisoformat(raw["checked_at"])
    target["reported_at"] = (checked + timedelta(seconds=skew)).isoformat()
    if skew == 1:
        assert fuel_update.compare(raw)["open"]
    else:
        with pytest.raises(ValueError, match="timestamp"):
            fuel_update.compare(raw)
