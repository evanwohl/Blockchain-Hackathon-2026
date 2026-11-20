import gzip
import json

import pytest

from freightos import customer_credit as credit


def pick(menu="both", fee=100, wallet=60000000, extra=0):
    return credit.compare(credit.saved(), menu, fee, wallet, extra)


def test_sources_collateral_and_same_rate_counterfactual():
    plan = pick()
    a, b, c = plan["options"]
    assert plan["choice"] == 9900
    assert [r["fits"] for r in plan["options"]] == [False, False, True]
    assert [r["cost_cents"] for r in (a, b, c)] == [323, 254, 208]
    assert [r["same_rate_cost_cents"] for r in (a, b, c)] == [323, 282, 241]
    for r in plan["options"]:
        assert r["reserve_cents"] + r["borrow_cents"] + r["advance_cents"] == plan["bill_cents"]
        assert r["fee_cents"] + r["invoice_left_cents"] + r["advance_cents"] == 20000
        assert r["payday_cash_cents"] + r["payoff_cents"] + r["reserve_cents"] == r["invoice_left_cents"]
        assert r["entry_lamports"] == r["pledge_lamports"] + r["setup_lamports"] + r["network_lamports"] + r["temporary_lamports"]
        assert r["retained_cents"] >= plan["holdback"]["cost_cents"]
    assert c["payday_cash_cents"] == 802
    assert pick(wallet=c["entry_lamports"] - 1)["choice"] is None
    assert pick(wallet=c["entry_lamports"])["choice"] == 9900
    assert pick(extra=802)["choice"] == 9900 and pick(extra=803)["choice"] is None


def test_only_contracts_the_customer_offers_can_win():
    assert pick("none")["choice"] is None
    assert pick("none", wallet=90000000)["choice"] == 0
    assert pick("half", wallet=71000000)["choice"] == 4950
    assert pick("full", fee=200, wallet=90000000)["choice"] == 9900
    assert pick(fee=200, wallet=90000000)["choice"] == 4950
    assert pick(fee=300, wallet=90000000)["choice"] == 0
    assert pick(fee=201, wallet=90000000)["options"][1]["fee_cents"] == 101
    with pytest.raises(ValueError):
        pick(menu="unconfirmed")
    with pytest.raises(ValueError):
        pick(wallet=True)


@pytest.mark.parametrize("fee,wallet,extra", [(100, 60000000, 0), (200, 90000000, 0), (100, 90000000, 670)])
def test_first_higher_fee_that_changes_the_recommendation(fee, wallet, extra):
    plan = pick(fee=fee, wallet=wallet, extra=extra)
    change = plan["next_change"]
    assert change is not None
    for value in range(fee, change["fee_cents"] + 1):
        viable = []
        for row in plan["options"]:
            actual = (row["advance_cents"] * value + 9899) // 9900
            cash = row["invoice_left_cents"] + row["fee_cents"] - actual
            if row["entry_lamports"] <= wallet and cash >= row["payoff_cents"] + row["reserve_cents"] + extra:
                viable.append((row["cost_cents"] - row["fee_cents"] + actual, row["entry_lamports"], row["advance_cents"]))
        winner = min(viable)[2] if viable else None
        assert winner == (change["advance_cents"] if value == change["fee_cents"] else plan["choice"])


def test_loan_evidence_binds_each_customer_amount():
    evidence = json.loads(gzip.decompress(credit.SAMPLE.read_bytes()))
    evidence["loans"]["9900"] = evidence["loans"]["4950"]
    with pytest.raises(ValueError, match="recorded transaction differs"):
        credit.replay(evidence)
