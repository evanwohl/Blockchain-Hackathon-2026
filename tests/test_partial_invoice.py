from copy import deepcopy
from decimal import Decimal, ROUND_CEILING

import pytest

from freightos import atomic_fuel as loan, next_stop


def test_optimized_draw_matches_every_possible_invoice_election():
    invoice, bill, cap = 35, 30, 12
    for fee in range(invoice + 1):
        for rate in map(Decimal, ("0", ".012", ".3")):
            for expense in (0, 2):
                candidates = []
                for gross in range(1, invoice):
                    discount = (gross * fee + invoice - 1) // invoice
                    net = gross - discount
                    draw = bill - net
                    if not 0 <= draw <= cap:
                        continue
                    paid = expense if draw else 0
                    if invoice - gross < draw + paid:
                        continue
                    missed = int((rate * draw).to_integral_value(rounding=ROUND_CEILING))
                    candidates.append((paid + missed + discount, draw, gross))
                actual, _ = loan.partial_invoice(invoice, bill, cap, fee, rate, expense, None)
                if candidates:
                    assert (actual["cost_cents"], actual["draw_cents"], actual["gross_cents"]) == min(candidates)
                else:
                    assert actual is None


def test_permission_expenses_and_customer_cash_reconcile():
    quote = loan.saved(40, 23, True)
    assert loan.compare_offer(quote, 300, 0)["choice"] == "early"
    plan = loan.compare_offer(quote, 300, 0, partial=True)
    assert plan["choice"] == "partial" and plan["partial_fee_limit_cents"] == 360
    assert loan.compare_offer(quote, 360, 0, partial=True)["choice"] == "partial"
    assert loan.compare_offer(quote, 361, 0, partial=True)["choice"] == "loan"
    p = plan["partial"]
    assert (p["gross_cents"], p["advance_cents"], p["draw_cents"], p["discount_cents"]) == (17333, 17073, 1927, 260)
    assert (p["regular_cents"], p["payday_cash_cents"], p["cost_cents"]) == (2667, 740, 266)
    assert p["advance_cents"] + p["draw_cents"] == quote["bill_cents"]
    assert p["advance_cents"] + p["discount_cents"] + p["regular_cents"] == 20000
    no_draw = loan.compare_offer(quote, 300, 0, partial=True, reserve_fee_cents=100)["partial"]
    assert no_draw["draw_cents"] == no_draw["expense_cents"] == 0
    assert loan.compare_offer(quote, 1001, 688)["choice"] == "short"
    reopened = loan.compare_offer(quote, 1001, 688, partial=True)
    assert reopened["choice"] == "partial" and reopened["partial"]["payday_cash_cents"] == 101
    assert loan.compare_offer(quote, 1000, 0, partial=True)["choice"] == "loan"
    assert loan.compare_offer(quote, 20000, 0, partial=True)["partial"] is None
    with pytest.raises(ValueError):
        loan.compare_offer(quote, 300, 0, partial=1)


@pytest.mark.parametrize(("draw", "remainder"), [(1927, 107500), (1937, 106500)])
def test_partial_reserve_evidence(draw, remainder):
    check = next_stop.advance_reserve(draw)
    assert check["source"] == "saved"
    assert check["vendor_raw"] == draw * 100 and check["retained_raw"] == remainder
    assert check["retained_raw"] >= check["holdback"]["cost_cents"] * 100
    tampered = deepcopy(next_stop.advance_recordings())
    tampered[str(draw)]["calls"][0]["response"] = "wrong cluster"
    with pytest.raises(ValueError, match="Devnet"):
        next_stop.advance_reserve(draw, evidence=tampered)


@pytest.mark.parametrize("limit", [None, 0, 1, 3, 7])
def test_negotiable_fee_matches_all_whole_cent_offers(limit):
    for expense in (0, 2, 7):
        workable = []
        for fee in range(36):
            plan, ceiling = loan.partial_invoice(35, 30, 12, fee, Decimal(".015"), expense, limit)
            if plan and (limit is None or plan["cost_cents"] <= limit):
                workable.append(fee)
        assert ceiling == max(workable, default=None)
