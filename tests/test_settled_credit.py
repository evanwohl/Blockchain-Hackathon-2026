import base64
import gzip
import json

import pytest
from solders.transaction import Transaction

from freightos import load_credit as loan


def evidence():
    return json.loads(gzip.decompress(loan.SAMPLE.read_bytes()))


def test_finalized_cycle_and_payoff_identity():
    sample = evidence()
    proof = loan.verify(sample)
    assert (proof["vendor_raw"], proof["repaid_raw"], proof["returned_lamports"]) == (70000, 70001, 3500000)
    assert proof["network_lamports"] == 10000
    assert proof["collateral_lamports"] == proof["returned_lamports"]
    owner = loan.Pubkey.from_string(sample["borrower"])

    def rpc(method, args):
        if method == "getGenesisHash":
            return loan.devnet.GENESIS
        if method == "getTransaction":
            assert args[0] == loan.BORROW and args[1]["commitment"] == "finalized"
            return sample["borrow"]
        assert method == "getMultipleAccounts"
        return {"value": [None, None]}

    assert loan.prepare_payoff(str(owner), loan.BORROW, rpc)["closed"]
    with pytest.raises(ValueError):
        loan.prepare_payoff(str(loan.credit.OWNER), loan.BORROW, rpc)


@pytest.mark.parametrize("change", ["advance", "vendor", "rent", "wire"])
def test_receipt_rejects_tampering(change):
    sample = evidence()
    if change == "advance":
        sample["advance_raw"] += 1
    elif change == "wire":
        sample["borrow"]["transaction"][0] = sample["repay"]["transaction"][0]
    elif change == "rent":
        sample["repay"]["meta"]["postBalances"][0] -= 1
    else:
        tx = Transaction.from_bytes(base64.b64decode(sample["borrow"]["transaction"][0]))
        index = tx.message.account_keys.index(loan.vendor())
        row, = [r for r in sample["borrow"]["meta"]["postTokenBalances"] if r["accountIndex"] == index]
        row["uiTokenAmount"]["amount"] = str(int(row["uiTokenAmount"]["amount"]) - 1)
    with pytest.raises(ValueError):
        loan.verify(sample)


def test_load_shortfall_depends_on_receipt_date_and_road_minimum():
    case = dict(opening_date="2026-11-01", opening_cents=83800, floor_cents=10000,
                fuel_date="2026-11-05", gallon_hundredths=20000, pump_mills=3725,
                freight_cents=300000, freight_date="2026-11-28", freight_status="expected",
                surcharge_cents=100, surcharge_date="2026-11-06", surcharge_status="expected")
    assert loan.load_terms(case) == (700, 23, 300100)
    assert loan.load_terms(dict(case, surcharge_date="2026-11-05")) == (600, 23, 300000)
    for edits in [dict(opening_cents=84500), dict(freight_date="2026-11-05"), dict(freight_status="unconfirmed")]:
        with pytest.raises(ValueError):
            loan.load_terms(dict(case, **edits))


def test_payoff_wire_enforces_user_reviewed_maximum():
    owner = loan.Pubkey.from_string(evidence()["borrower"])
    ops = loan.instructions(owner, 3500000, 70000, repay=True, repay_cap=70002)
    original = loan.instructions(owner, 3500000, 70000, repay=True)
    assert ops[:-2] == original[:-2] and ops[-1] == original[-1]
    assert ops[-2].program_id == loan.credit.KLEND and ops[-2].accounts == original[-2].accounts
    assert ops[-2].data[:8] == original[-2].data[:8]
    assert int.from_bytes(ops[-2].data[8:16], "little") == 70002
    assert ops[-2].data[16:] == original[-2].data[16:]
