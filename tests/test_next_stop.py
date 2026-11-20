import gzip
import json

import pytest

from freightos import devnet, next_stop, public_offer


@pytest.fixture(scope="module")
def proofs():
    return devnet.verify(json.loads(devnet.SAMPLE.read_bytes())), public_offer.verify(json.loads(public_offer.SAMPLE.read_bytes()))


def transcript():
    return json.loads(gzip.decompress(next_stop.SNAPSHOT.read_bytes()))["calls"]


def captured(calls):
    def rpc(method, params):
        record = calls.pop(0)
        assert (method, params) == (record["method"], record["params"])
        return record["response"]
    return rpc


def test_saved_next_stop_reconciles_the_actual_unsigned_transaction():
    result = next_stop.saved()
    assert result["first_shares_raw"] == result["offer_shares_raw"] == 150100
    assert result["eligible_shares_raw"] == result["available_raw"] == 300200
    assert result["vendor_raw"] == 200000 and result["wallet_increase_raw"] == 100200
    assert result["shortfall_raw"] == 0
    assert result["wallet_after_settlement_raw"] == 0
    assert result["network_fee_lamports"] == 5000


def test_shortfall_uses_only_the_two_receipts_and_skips_payment(proofs):
    calls = transcript()[:2]
    result = next_stop.preview(*proofs, 2500, 1000, captured(calls))
    assert result["shortfall_raw"] == 49800
    assert result["vendor_raw"] == result["wallet_increase_raw"] == 0
    assert not calls


def test_missing_share_account_is_a_shortfall(proofs):
    calls = transcript()[:1]
    calls[0]["response"]["value"] = None
    result = next_stop.preview(*proofs, 2000, 1000, captured(calls))
    assert result["eligible_shares_raw"] == 0 and result["shortfall_raw"] == 300000


@pytest.mark.parametrize("field", ["shares_before_setup_raw", "shares_after_raw", "receipt", "share_account"])
def test_remainders_must_belong_to_two_distinct_reconciled_payments(proofs, field):
    first, second = proofs
    changed = {**second, field: first["receipt"] if field == "receipt" else 0}
    with pytest.raises(ValueError, match="reconcile"):
        next_stop.preview(first, changed, 2000, 1000, captured([]))


@pytest.mark.parametrize("account", ["payer", "destination", "custody", "shares"])
def test_payment_requires_matching_wallet_vendor_custody_and_burn(proofs, account):
    calls = transcript()
    method = calls[-1]
    message = devnet.Transaction.from_bytes(devnet.base64.b64decode(method["params"][0])).message
    plan = next_stop.payment()
    address = plan.cash if account == "payer" else getattr(plan, account)
    rows = method["response"]["value"]["postTokenBalances"]
    amount = next(r for r in rows if r["accountIndex"] == message.account_keys.index(address))["uiTokenAmount"]
    amount["amount"] = str(int(amount["amount"]) + 1)
    with pytest.raises(ValueError, match="reconcile"):
        next_stop.preview(*proofs, 2000, 1000, captured(calls))


@pytest.mark.parametrize(("fuel", "road"), [(0, 1), (True, 0), (-1, 0), (1, 1.5), (1, -2)])
def test_invalid_amounts_do_not_start_a_simulation(proofs, fuel, road):
    with pytest.raises(ValueError):
        next_stop.preview(*proofs, fuel, road, captured([]))


def two_stop_calls():
    return json.loads(gzip.decompress(next_stop.TWO_STOPS.read_bytes()))["calls"]


def test_gallon_budget_limits_the_first_fill_and_leaves_redeemable_shares():
    result = next_stop.saved(True)
    fuel = result["holdback"]
    assert (fuel["trip_cents"], fuel["trip_gallons"], fuel["cost_cents"]) == (70634, 210, 1010)
    assert fuel["event"] == "WTI2611192200"
    assert result["budget_limit_cents"] == 1492 and result["shortfall_raw"] == 800
    assert result["wallet_increase_raw"] == 50000 and result["vendor_raw"] == 149200
    assert result["retained_raw"] == 101000 == result["retained_shares_raw"]
    assert result["wallet_increase_raw"] + result["vendor_raw"] + result["retained_raw"] == result["available_raw"]
    assert result["vendor_raw"] + 100 + result["wallet_increase_raw"] + result["retained_raw"] > result["available_raw"]


@pytest.mark.parametrize("change", ["later transfer", "earlier transfer", "custody", "wallet", "stale slot", "starting cash"])
def test_later_fuel_must_be_spendable_after_the_first_withdrawal(proofs, change):
    calls = two_stop_calls()
    record = calls[-1]
    value = record["response"]["value"]
    plan = next_stop.payment()
    if change.endswith("transfer"):
        position = 1 if change == "earlier transfer" else 3
        group = next(row for row in value["innerInstructions"] if row["index"] == position)
        transfer = next(ix for ix in group["instructions"] if ix.get("parsed", {}).get("type") == "transferChecked")
        transfer["parsed"]["info"]["tokenAmount"]["amount"] = "1"
    elif change == "stale slot":
        record["response"]["context"]["slot"] = 1
    else:
        message = devnet.Transaction.from_bytes(devnet.base64.b64decode(record["params"][0])).message
        account = plan.custody if change == "custody" else plan.cash
        side = "preTokenBalances" if change == "starting cash" else "postTokenBalances"
        balance = next(row for row in value[side] if row["accountIndex"] == message.account_keys.index(account))
        balance["uiTokenAmount"]["amount"] = "1"
    held = next_stop.fuel_holdback(3, next_stop.fuel_update.saved())
    with pytest.raises(ValueError):
        next_stop.preview(*proofs, 1500, 500, captured(calls), holdback=held)


def test_sized_single_payment_keeps_the_unspent_shares(proofs):
    calls = two_stop_calls()
    result = next_stop.preview(*proofs, 1492, 500, captured(calls), keep_shares=True)
    assert not calls
    assert result["vendor_raw"] == 149200 and result["wallet_increase_raw"] == 50000
    assert result["retained_shares_raw"] == result["retained_raw"] == 101000
    assert result["available_raw"] == 300200


def test_sized_payment_can_use_the_entire_eligible_balance(proofs):
    calls = transcript()
    result = next_stop.preview(*proofs, 2000, 1002, captured(calls), keep_shares=True)
    assert not calls
    assert result["retained_raw"] == result["retained_shares_raw"] == 0
    assert result["wallet_increase_raw"] == 100200


def test_no_first_fill_when_cash_and_future_fuel_exceed_the_available_balance(proofs):
    held = next_stop.fuel_holdback(10, next_stop.fuel_update.saved())
    calls = two_stop_calls()[:2]
    result = next_stop.preview(*proofs, 1500, 500, captured(calls), holdback=held)
    assert result["budget_limit_cents"] == 0 and result["retained_raw"] is None
    assert result["vendor_raw"] == 0 and result["shortfall_raw"] > 0
    assert not calls


@pytest.mark.parametrize("gallons", [True, -1, 0, 1.25, "3"])
def test_gallon_input_is_a_positive_whole_count(gallons):
    with pytest.raises(ValueError):
        next_stop.fuel_holdback(gallons, next_stop.fuel_update.saved())


@pytest.mark.parametrize(("price", "stress", "base", "total"), [
    (3999, False, 1200, 1200), (3999, True, 1200, 1249),
    (None, False, 1010, 1010), (None, True, 1010, 1058),
])
def test_later_budget_anchors_diesel_then_applies_only_the_incremental_oil_move(price, stress, base, total):
    held = next_stop.fuel_holdback(3, next_stop.fuel_update.saved(), pump_mills=price, oil_rise=stress)
    assert (held["base_cents"], held["cost_cents"], held["extra_cents"]) == (base, total, total - base)


def test_oil_already_above_threshold_keeps_the_posted_diesel_price():
    market = {**next_stop.fuel_update.saved(), "close": "93.55"}
    held = next_stop.fuel_holdback(3, market, pump_mills=3999, oil_rise=True)
    assert held["base_cents"] == held["cost_cents"] == 1200


def test_pump_sign_limit_is_the_largest_thousandth_affordable_on_verified_remaining_proceeds():
    result = next_stop.saved(True)
    mills = result["covered_pump_mills"]
    assert mills == 3366
    gallons = result["holdback"]["gallons"]
    remaining_cents = result["retained_raw"] // result["raw_units_per_cent"]
    assert mills * gallons <= remaining_cents * 10 < (mills + 1) * gallons


@pytest.mark.parametrize(("price", "stress"), [(0, False), (False, False), (3.999, False), (1_000_000, False), (3999, 1)])
def test_invalid_pump_price_or_scenario_is_rejected(price, stress):
    with pytest.raises(ValueError):
        next_stop.check_fuel_inputs(3, price, stress)
