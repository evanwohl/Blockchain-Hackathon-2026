import base64
import json

import pytest
from solders.signature import Signature
from solders.transaction import Transaction

from freightos import public_offer


def recorded():
    return json.loads(public_offer.SAMPLE.read_bytes())


def test_customer_and_reserve_settle_the_entire_priced_fuel_bill():
    proof = public_offer.verify(recorded())
    assert proof["received_raw"] == proof["reserve_paid_raw"] + proof["customer_paid_raw"] == 7450000
    assert proof["customer_kept_raw"] == 50000
    assert proof["shares_burned_raw"] == 5700000
    assert proof["decision"]["fees_cents"] == 849
    assert proof["decision"]["highest_fee_cents"] == 994
    assert proof["decision"]["least_saved_cents"] == 496


@pytest.mark.parametrize("party", ["customer", "vendor_owner", "payer"])
def test_both_wallets_and_vendor_must_reconcile(party):
    run = recorded()
    owner = run["decision"]["customer"] if party == "customer" else run[party]
    row = next(b for b in run["transactions"]["settle"]["meta"]["postTokenBalances"]
               if b["owner"] == owner and b["mint"] == run["decision"]["mint"])
    row["uiTokenAmount"]["amount"] = str(int(row["uiTokenAmount"]["amount"]) + 1)
    with pytest.raises(ValueError, match="reconcile"):
        public_offer.verify(run)


def test_customer_signature_is_required_even_when_the_driver_signed():
    run = recorded()
    item = run["transactions"]["settle"]
    tx = Transaction.from_bytes(base64.b64decode(item["transaction"][0]))
    assert len(tx.signatures) == 2
    changed = Transaction.populate(tx.message, [tx.signatures[0], Signature.default()])
    item["transaction"][0] = base64.b64encode(bytes(changed)).decode()
    with pytest.raises(ValueError, match="signed instructions"):
        public_offer.verify(run)


def test_repricing_and_live_receipt_checks():
    run = recorded()
    def rpc(method, params):
        if method == "getGenesisHash":
            return public_offer.devnet.GENESIS
        if method == "getTransaction":
            assert params[1]["commitment"] == "finalized"
            return next(tx for tx in run["transactions"].values() if tx["signature"] == params[0])
        assert method == "getAccountInfo" and params[1]["minContextSlot"] == run["transactions"]["settle"]["slot"]
        return run["receipt"]

    assert public_offer.verify(run, rpc)["source"] == "public-rpc"
    run["receipt"]["context"]["slot"] = 0
    with pytest.raises(ValueError, match="predates"):
        public_offer.verify(run, rpc)
    run["decision"]["highest_fee_cents"] += 1
    with pytest.raises(ValueError, match="priced offer"):
        public_offer.verify(run)
