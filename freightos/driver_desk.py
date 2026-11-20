"""Price a driver's own single-stop bill using the recorded mainnet funding sources."""
from decimal import ROUND_HALF_UP
from fractions import Fraction

from hedging.quick_pay import integer
from freightos import collateral_path, fuel_floor, mainnet_loan, mainnet_stop, payday_policy

MAX_BILL = 250_000
VENDOR_FEE_LAMPORTS = 5_000


def quote(data):
    bill = integer(data.get("bill"), "fuel bill in cents", MAX_BILL)
    if bill == 0:
        raise ValueError("Enter a positive fuel bill")
    invoice = integer(data.get("invoice"), "freight invoice", 100_000_000)
    values = {k: integer(data.get(k), k, 100_000_000) for k in ("budget", "cash", "stable", "target")}
    menu, fee, amount = data.get("offer_menu"), data.get("offer_fee"), data.get("offer_amount")
    mainnet_stop.validate_offer(menu, fee, amount, invoice)
    if menu == "counter" and amount + fee > invoice:
        raise ValueError("Early cash and the customer fee exceed the freight invoice")
    stress = integer(data.get("stress"), "additional SOL price", 10_000_000)
    if not stress:
        raise ValueError("Enter a positive SOL price")
    first_loan = data.get("first_loan", False)
    if type(first_loan) is not bool:
        raise ValueError("Choose whether to include first-loan setup")
    try:
        return _price(bill, invoice, values, stress, (menu, fee, amount), first_loan)
    except (OSError, ValueError, KeyError, ArithmeticError, TypeError, RuntimeError) as exc:
        raise RuntimeError("Recorded funding terms could not be verified") from exc


def _price(bill, invoice, values, stress, offer, first_loan):
    lending = mainnet_loan.saved()
    reference = int((lending["sol_price"] * 100).to_integral_value(rounding=ROUND_HALF_UP))
    cases, _ = payday_policy.price_cases(reference, stress)
    prices = [case["price_cents"] for case in cases]
    plan = dict(bill_cents=bill, invoice_cents=invoice, receipt_cents=invoice, reference_cents=reference,
                high_price_cents=max(prices), low_price_cents=min(prices), floor_cents=values["target"])
    observation, pools = mainnet_stop.saved()
    cap = values["budget"] * mainnet_stop.LAMPORTS // (100 * Fraction(lending["sol_price"]))
    gap = max(0, bill - values["cash"] - values["stable"])
    # Even a fee-free swap at the highest opening spot cannot return more than this much USDC.
    max_sale = cap * max(p["price"]**2 for p in pools) // (fuel_floor.Q64**2 * mainnet_stop.SCALE)
    costs, _ = mainnet_stop.split_costs(pools, min(gap, max_sale))
    loan = mainnet_loan.compare(plan, costs, values["stable"], values["cash"], cap, cap, VENDOR_FEE_LAMPORTS, observation["rent"],
                                lending, [c["price_cents"] for c in cases if "start" in c],
                                offer_terms=offer, path=collateral_path.saved(),
                                setup=mainnet_loan.first_loan_setup() if first_loan else None)
    return {**plan, "mainnet": {"slot": observation["accounts"]["context"]["slot"], "secured_loan": loan}}
