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
