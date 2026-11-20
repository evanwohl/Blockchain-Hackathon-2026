import gzip
import json
from copy import deepcopy

import pytest

from freightos import customer_credit, fuel_swap as sale


def test_two_pools_same_vendor_and_cost_threshold():
    checked = sale.saved(0)
    result = sale.compare(checked, 100, 20000000, 0)
    best = result["sale"]
    assert result["sale_short_lamports"] == 0 < result["loan_short_lamports"]
    assert best["wallet_raw"] == 9900 and best["reserve_raw"] == 194400 and best["swap_raw"] == 1695700
    assert best["wallet_raw"] + best["reserve_raw"] + best["swap_raw"] == best["vendor_raw"] == 1900000
    assert best["retained_raw"] == result["holdback_cents"] * result["scale"]
    assert [r["spent_lamports"] for r in result["pools"]] == [11930887, 172690362]
    assert best["entry_lamports"] == best["limit_lamports"] + best["rent_lamports"] + best["fee_lamports"]
    assert sale.compare(checked, 100, best["entry_lamports"]-1, 0)["sale_short_lamports"] == 1
    first = result["first_higher_cents"]
    assert first == 14476
    assert sale.compare(checked, 100, 20000000, 0, first-1)["difference_cents"] == 0
    assert sale.compare(checked, 100, 20000000, 0, first)["difference_cents"] == -2


@pytest.mark.parametrize("customer", [4950, 9900])
def test_contribution_settles_only_the_remaining_fuel(customer):
    checked = sale.saved(customer)
    result = sale.compare(checked, 201, 60000000, 45)
    sale_leg = result["sale"]
    assert sale_leg["vendor_raw"] + customer * result["scale"] == 1900000
    invoice = 20000 - customer - customer_credit.customer_fee(customer, 201)
    assert result["loan_remaining_cents"] + result["repay_cents"] + checked["loan"]["reserve_raw"] // result["scale"] == invoice
    assert result["sale_remaining_cents"] - result["loan_remaining_cents"] == result["difference_cents"]


def test_more_wallet_cash_reduces_the_reserve_before_selling_sol(monkeypatch):
    reserve = {"fuel_cents": 19000, "raw_units_per_cent": 100, "vendor_raw": 194400, "eligible_shares_raw": 20000, "holdback": {}}
    assert sale.spendable_reserve(reserve, 9900, 715699, None) is reserve
    smaller = {**reserve, "vendor_raw": 194300, "fuel_cents": 1943}
    monkeypatch.setattr(sale.reserve_credit, "prior_payments", lambda: ({}, {}))
    monkeypatch.setattr(sale.next_stop, "preview", lambda a,b,c,d,rpc,holdback: smaller if c == 1943 else {})
    assert sale.spendable_reserve(reserve, 9900, 715700, None)["vendor_raw"] == 194300
    free = sale.spendable_reserve(reserve, 9900, 910099, None)
    assert free["vendor_raw"] == 0 and free["retained_shares_raw"] == 20000


def test_replay_rejects_changed_vendor_receipt_and_changed_customer():
    run = json.loads(gzip.decompress(sale.SAMPLE.read_bytes()))["0"]
    with pytest.raises(ValueError, match="customer payment"):
        sale.replay(run, 9900)
    damaged = deepcopy(run)
    sim = next(x for x in damaged["swap"] if x["method"] == "simulateTransaction")
    sim["response"]["value"]["fee"] += 1
    with pytest.raises(ValueError, match="pool proceeds"):
        sale.replay(damaged, 0)
