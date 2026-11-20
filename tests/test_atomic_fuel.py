import base64
from copy import deepcopy

import pytest
from solders.transaction import VersionedTransaction

from freightos import atomic_fuel as fuel, load_credit


@pytest.mark.parametrize("days", [23, 30])
@pytest.mark.parametrize("drop", [20, 40, 60])
def test_both_venues_and_remaining_gallons_reconcile(days, drop):
    q = fuel.saved(drop, days)
    assert q["vendor_raw"] == q["reserve_raw"] + q["borrow_raw"] == 1900000
    assert q["borrow_raw"] == 1705600 and q["retained_raw"] >= 105800
    assert q["slot"] <= q["refused_slot"] <= q["remainder_slot"]
    assert 1232 < q["wire_bytes"] <= 4096
    assert q["cost_room_cents"] + q["missed_yield_cents"] + q["interest_cents"] == 1000
    if days == 23 and drop == 40:
        assert q["cost_room_cents"] == 876 and q["repay_cents"] == 17175


@pytest.mark.parametrize("tamper", ["wire", "refusal", "reserve", "vendor", "rollback", "retained"])
def test_replay_detects_changed_transaction_or_balances(tamper):
    calls = deepcopy(fuel.snapshot()["cases"]["40-23"])
    opening, refused, remaining = calls[-3:]
    if tamper == "wire":
        opening["params"][0] = refused["params"][0]
    elif tamper == "refusal":
        refused["response"]["value"]["err"] = None
    elif tamper == "rollback":
        refused["response"]["value"]["postBalances"][1] -= 1
    else:
        plan = fuel.next_stop.payment()
        call, account = (remaining, plan.cash) if tamper == "retained" else (opening, plan.custody if tamper == "reserve" else plan.destination)
        msg = VersionedTransaction.from_bytes(base64.b64decode(call["params"][0])).message
        index = msg.account_keys.index(account)
        row, = [r for r in call["response"]["value"]["postTokenBalances"] if r["accountIndex"] == index]
        row["uiTokenAmount"]["amount"] = str(int(row["uiTokenAmount"]["amount"]) - 1)
    with pytest.raises(ValueError):
        fuel.replay(fuel.saved_reserve(), 40, 23, calls)


def test_payoff_reconstructs_only_the_combined_opening():
    accepted, refused, _ = fuel.snapshot()["cases"]["40-23"][-3:]
    tx = VersionedTransaction.from_bytes(base64.b64decode(accepted["params"][0]))
    meta = accepted["response"]["value"]
    assert fuel.opening_amounts(tx.message, meta, fuel.credit.OWNER) == (61110000, 1705600)
    changed = VersionedTransaction.from_bytes(base64.b64decode(refused["params"][0]))
    with pytest.raises(ValueError):
        fuel.opening_amounts(changed.message, meta, fuel.credit.OWNER)

    def unsigned(method, args):
        if method == "getGenesisHash":
            return fuel.devnet.GENESIS
        assert method == "getTransaction" and args[1]["maxSupportedTransactionVersion"] == 1
        return {"transaction": [accepted["params"][0], "base64"], "meta": meta}

    with pytest.raises(ValueError, match="signed combined payment"):
        load_credit.opening(str(tx.signatures[0]), fuel.credit.OWNER, unsigned)


@pytest.mark.parametrize("days", [23, 30])
@pytest.mark.parametrize("drop", [20, 40, 60])
def test_one_pledge_covers_both_paydays(days, drop):
    q = fuel.saved(drop, days, True)
    stress = q["payday_stress"]
    assert stress["days"] == days + 7 and stress["repay_cents"] > q["repay_cents"]
    assert stress["protected"] and q["pledge_lamports"] == stress["required_lamports"]
    assert q["pledge_lamports"] >= stress["ordinary_pledge_lamports"]
    assert stress["stress_ltv_bps"] <= stress["target_ltv_bps"]
    accepted = fuel.snapshot(True)["cases"][f"{drop}-{days}"][-3]
    tx = VersionedTransaction.from_bytes(base64.b64decode(accepted["params"][0]))
    assert fuel.opening_amounts(tx.message, accepted["response"]["value"], fuel.credit.OWNER) == (q["pledge_lamports"], q["borrow_raw"])


def test_customer_fee_and_payday_cash_boundaries():
    q = fuel.saved(40, 23, True)
    assert q["setup_lamports"] == q["obligation_rent_lamports"] + q["profile_rent_lamports"]
    assert q["payday_stress"]["repay_cents"] == 17369
    for fee, other, choice, cash in [(319, 0, "early", 687), (320, 0, "loan", 687),
                                    (1001, 687, "loan", 0), (1001, 688, "short", -1),
                                    (1000, 688, "early", -1)]:
        result = fuel.compare_offer(q, fee, other)
        assert (result["choice"], result["payday_cash_cents"]) == (choice, cash)
    assert fuel.compare_offer(fuel.saved(40, 23), 320, 0)["loan_cost_cents"] == 124
    with pytest.raises(ValueError):
        fuel.compare_offer(q, 20001, 0)
