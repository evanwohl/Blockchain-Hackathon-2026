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


class QuoteRPC:
    def __init__(self, run, balance=150100, payout=None):
        self.plan = devnet.PublicPayment(run, devnet.Pubkey.from_string(run["payer"]))
        self.balance = balance
        self.payout = payout or (lambda units: units)
        self.simulations = []
        self.slot = run["transactions"]["payment"]["slot"]

    def __call__(self, method, params):
        plan = self.plan
        self.slot += 1
        if method == "getAccountInfo":
            assert params[0] == str(plan.shares) and params[1]["commitment"] == "finalized"
            info = {"owner": str(plan.payer), "mint": str(devnet.SHARES), "tokenAmount": {"decimals": 6, "amount": str(self.balance)}}
            account = {"owner": str(devnet.TOKEN), "executable": False, "data": {"parsed": {"type": "account", "info": info}}}
            return {"context": {"slot": self.slot}, "value": account if self.balance is not None else None}
        assert method == "simulateTransaction"
        wire, config = params
        assert config["replaceRecentBlockhash"] and config["sigVerify"] is False
        assert config["commitment"] == "finalized" and config["minContextSlot"] == self.slot - 1
        tx = devnet.Transaction.from_bytes(devnet.base64.b64decode(wire))
        assert not any(bytes(tx.signatures[0]))
        units = int.from_bytes(tx.message.instructions[-1].data[8:], "little")
        expected = devnet.vault_instruction(plan.route, plan.payer, plan.cash, plan.shares, units, venue=devnet.VENUE)
        assert tx.message == devnet.Message.new_with_blockhash([devnet.set_compute_unit_limit(400000), expected], plan.payer, devnet.Hash.default())
        self.simulations.append(units)
        def row(account, mint, raw):
            return {"accountIndex": tx.message.account_keys.index(account), "mint": str(mint), "owner": str(plan.payer),
                    "programId": str(devnet.TOKEN), "uiTokenAmount": {"decimals": 6, "amount": str(raw)}}
        return {"context": {"slot": self.slot}, "value": {"err": None, "fee": 5000,
                "preTokenBalances": [row(plan.cash, devnet.MINT, 700000), row(plan.shares, devnet.SHARES, self.balance)],
                "postTokenBalances": [row(plan.cash, devnet.MINT, 700000 + self.payout(units)),
                                      row(plan.shares, devnet.SHARES, self.balance - units)]}}


def test_preview_checks_unsigned_public_withdrawal_and_both_token_deltas():
    run = json.loads(devnet.SAMPLE.read_bytes())
    proof = {**devnet.verify(run), "source": "public-rpc"}
    rpc = QuoteRPC(run)
    road = devnet.reserve_preview(run, proof, "road", rpc)
    assert rpc.simulations == [150100, 100000]
    assert (road["eligible_shares_raw"], road["return_raw"], road["target_raw"]) == (150100, 100000, 100000)
    rpc = QuoteRPC(run, balance=200000, payout=lambda units: 2 * units - 500)
    road = devnet.reserve_preview(run, proof, "road", rpc)
    assert rpc.simulations[0] == 150100
    assert road["return_raw"] == 100000 and road["shares_used_raw"] == 50250
    assert road["eligible_shares_raw"] == 150100
    rpc = QuoteRPC(run, balance=98000)
    road = devnet.reserve_preview(run, proof, "road", rpc)
    assert road["return_raw"] == 98000 and rpc.simulations == [98000]
    for balance in (None, 0):
        empty = QuoteRPC(run, balance=balance)
        assert devnet.reserve_preview(run, proof, "all", empty)["return_raw"] == 0
        assert empty.simulations == []


@pytest.mark.parametrize("change", ["mint", "amount", "duplicate", "missing", "changed shares", "stale slot", "program error"])
def test_public_preview_fails_closed_on_inconsistent_simulation(change):
    run = json.loads(devnet.SAMPLE.read_bytes())
    proof = {**devnet.verify(run), "source": "public-rpc"}
    rpc = QuoteRPC(run)
    def changed(method, params):
        result = rpc(method, params)
        if method == "simulateTransaction":
            rows = result["value"]["postTokenBalances"]
            if change == "mint":
                rows[0]["mint"] = str(devnet.SHARES)
            elif change == "amount":
                rows[0]["uiTokenAmount"]["amount"] = "650000"
            elif change == "duplicate":
                rows.append(rows[0])
            elif change == "missing":
                rows.pop()
            elif change == "changed shares":
                result["value"]["preTokenBalances"][1]["uiTokenAmount"]["amount"] = "160000"
            elif change == "stale slot":
                result["context"]["slot"] = 1
            else:
                result["value"]["err"] = "failed"
        return result
    with pytest.raises(ValueError):
        devnet.reserve_preview(run, proof, "road", changed)
