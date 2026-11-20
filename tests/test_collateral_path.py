import gzip
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction

import pytest

from freightos import collateral_path, mainnet_loan, road_cash


def test_frontier_matches_each_valid_observation_with_accrual_and_loan_rounding():
    path = collateral_path.saved()
    assert (path["windows"], path["observations"], path["omitted"], path["max_gap_hours"]) == (9, 211, 9, 36)
    terms = mainnet_loan.saved()
    marks = collateral_path.barriers(path, terms["sol_price"], terms["apr"] * 2)
    assert len(marks) == 6
    for raw in (1, 10000, 33910000, 35230000, 88840000):
        ratios = []
        for samples in path["traces"].values():
            for step, ratio in samples:
                price = int(Fraction(terms["sol_price"]) * ratio * 100)
                debt = (raw * (terms["apr"] * step / 365).exp()).to_integral_value(rounding=ROUND_CEILING)
                ratios.append(debt / price)
        assert collateral_path.peak(raw, marks)[0] == max(ratios)
    quote = road_cash.evaluate(8000, 7500, 20000, 300, 15000)["mainnet"]["secured_loan"]
    selected = quote["combined"]["plan"]
    check = selected["path_check"]
    assert max(r["ltv_bps"] for r in check["samples"]) == check["peak_ltv_bps"] == 7000
    assert check["ticker"] == "SOL2611142100" and check["hours"] == 600
    assert check["samples"][-1] == dict(hours=720, price_cents=4959, ltv_bps=6772)
    old = collateral_path.replay(path, marks, 35230000, 1018500000, terms)
    assert old["peak_ltv_bps"] == 7273
    peak, mark = collateral_path.peak(33910000, marks)
    adjusted = peak * terms["usd_price"] * terms["factor"] * Decimal(10**7)
    assert adjusted / selected["pledge_lamports"] <= 7000 < adjusted / (selected["pledge_lamports"] - 100000)


def test_changing_path_evidence_withholds_the_loan_and_keeps_customer_only_quote(monkeypatch):
    damaged = gzip.decompress(collateral_path.EVIDENCE.read_bytes()) + b" "
    monkeypatch.setattr(collateral_path, "saved", lambda: collateral_path.verify(damaged))
    with pytest.raises(ValueError, match="evidence changed"):
        collateral_path.saved()
    m = road_cash.evaluate(8000, 7500, 20000, 300, 15000)["mainnet"]
    assert m["secured_loan"] is None and m["early_freight"]["smallest"]["advance_cents"] == 8893
