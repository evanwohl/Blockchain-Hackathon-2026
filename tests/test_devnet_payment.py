import json

import pytest

from freightos import devnet
from freightos.funding import demo_plans


def test_signed_public_example_matches_the_driver_plan():
    run = json.loads(devnet.SAMPLE.read_bytes())
    result = devnet.verify(run)
    plan = demo_plans()[0]["quick pay"]["oil stress"]
    decision = result["decision"]
    assert [decision[k] for k in ("bill_cents", "reserve_cents", "outside_cents", "fees_cents", "input_sha256")] == [
        plan[k] for k in ("bill_cents", "payment_cents", "other_funds_cents", "cost_cents", "input_sha256")
    ]
    assert decision["comparison_fees_cents"] == plan["alternative_cost_cents"]
    assert result["vendor_received_raw"] == plan["payment_cents"] * 100 == 5_550_000
    assert result["source"] == "saved-receipt"


def test_public_example_rejects_wrong_network_or_changed_vendor_proceeds():
    run = json.loads(devnet.SAMPLE.read_bytes())
    with pytest.raises(ValueError, match="Devnet"):
        devnet.verify(run, lambda method, params: "other cluster")
    balances = run["transactions"]["payment"]["meta"]["postTokenBalances"]
    vendor = next(r for r in balances if r["owner"] == run["vendor_owner"])
    vendor["uiTokenAmount"]["amount"] = "5550001"
    with pytest.raises(ValueError, match="Vendor proceeds"):
        devnet.verify(run)


def test_public_example_rejects_changed_approval_and_receipt():
    run = json.loads(devnet.SAMPLE.read_bytes())
    run["decision"]["fees_cents"] += 1
    with pytest.raises(ValueError, match="signed instructions"):
        devnet.verify(run)
    run = json.loads(devnet.SAMPLE.read_bytes())
    run["receipt"]["value"]["owner"] = str(devnet.VAULT_PROGRAM)
    with pytest.raises(ValueError, match="public receipt"):
        devnet.verify(run)
