import base64
import gzip
import json

import pytest

from freightos import credit_exit as exit_check


@pytest.fixture
def example():
    return json.loads(gzip.decompress(exit_check.SNAPSHOT.read_bytes()))["2140000-59800"]


def replay(run):
    return exit_check.verify(run, 2_140_000, 59_800, 40, 23)


def test_saved_payoff_matches_cash_and_lending_transfers(example):
    result = replay(example)
    assert result["slot"] == 524211032
    assert (result["returned_lamports"], result["refunded_lamports"], result["held_lamports"]) == (2_140_000, 17_637_760, 5_892_800)
    assert (result["entry_cash_cents"], result["refund_cents"], result["net_cash_cost_cents"]) == (171, 128, 46)
    assert result["exit_fee_lamports"] == 5000
    assert len(base64.b64decode(example["wire"])) <= 1232


def test_all_bundled_wallet_draws_have_payoff_evidence():
    samples = json.loads(gzip.decompress((exit_check.credit.DATA / "payday_credit.json.gz").read_bytes()))
    for key, sample in samples.items():
        days = int(key.split("-")[3])
        result = exit_check.check(sample["run"]["collateral"], sample["cents"] * 100, 40, days, recorded=True)
        assert result["net_cash_cost_cents"] >= result["cost_cents"] > 0


@pytest.mark.parametrize("damage", [
    "principal", "collateral", "mint", "recipient", "program", "balance", "metadata", "closure", "wire",
    "fee_message", "lookup", "old_response", "old_fee", "missing_fee", "other_loan",
])
def test_changed_payoff_evidence_fails_closed(example, damage):
    value = example["response"]["value"]
    group = next(g for g in value["innerInstructions"] if g["index"] == 15)
    repay, release = [x for x in group["instructions"] if x.get("parsed", {}).get("type") == "transferChecked"]
    if damage == "principal":
        repay["parsed"]["info"]["tokenAmount"]["amount"] = "0"
    elif damage == "collateral":
        release["parsed"]["info"]["tokenAmount"]["amount"] = "2139999"
    elif damage == "mint":
        repay["parsed"]["info"]["mint"] = str(exit_check.credit.SOL)
    elif damage == "recipient":
        release["parsed"]["info"]["destination"] = repay["parsed"]["info"]["destination"]
    elif damage == "program":
        repay["programId"] = str(exit_check.credit.KLEND)
    elif damage == "balance":
        value["postBalances"][0] += 5000
    elif damage == "metadata":
        created = next(g for g in value["innerInstructions"] if g["index"] == 3)
        created["instructions"][0]["parsed"]["info"]["lamports"] -= 1
    elif damage == "closure":
        value["accounts"][0]["lamports"] = 1
    elif damage in ("wire", "fee_message"):
        example[damage] = base64.b64encode(b"wrong message").decode()
    elif damage == "lookup":
        value["loadedAddresses"]["readonly"].reverse()
    elif damage == "old_response":
        example["response"]["context"]["slot"] = 1
    elif damage == "old_fee":
        example["fee"]["context"]["slot"] = 1
    elif damage == "missing_fee":
        example["fee"]["value"] = None
    else:
        example["opening"]["collateral"] += 10_000
    with pytest.raises(ValueError):
        replay(example)
