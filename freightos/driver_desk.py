"""Price a driver's own single-stop bill using the recorded mainnet funding sources."""
from decimal import ROUND_HALF_UP
from fractions import Fraction

from hedging.quick_pay import integer
from freightos import collateral_path, fuel_floor, mainnet_loan, mainnet_stop, payday_policy, route_booking

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
    state = data.get("borrower_state", "new" if first_loan else "ready")
    if state not in ("ready", "new", "returning") or ("first_loan" in data and first_loan != (state == "new")):
        raise ValueError("Choose one borrowing setup")
    stops = integer(data.get("stops", 1), "number of repeat stops", 12)
    if stops == 0 or (stops > 1 and state != "new"):
        raise ValueError("Compare 2–12 identical stops starting with a first loan")
    booked = None
    if "booked" in data:
        booked = integer(data["booked"], "confirmed stops", stops)
        if booked < 1 or stops < 2:
            raise ValueError("Enter at least one confirmed stop in a recurring route")
    try:
        return _price(bill, invoice, values, stress, (menu, fee, amount), state, stops, booked)
    except (OSError, ValueError, KeyError, ArithmeticError, TypeError, RuntimeError) as exc:
        raise RuntimeError("Recorded funding terms could not be verified") from exc


def _price(bill, invoice, values, stress, offer, state, stops, booked):
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
                                setup=mainnet_loan.borrower_setup(returning=state == "returning") if state != "ready" else None, repeat=stops > 1)
    result = {**plan, "mainnet": {"slot": observation["accounts"]["context"]["slot"], "secured_loan": loan}}
    if stops > 1:
        result["series"] = recurring_plan(loan["combined"]["best_loan"], loan["repeat_plan"],
                                          loan["combined"]["most_cash_without_loan"], stops, plan)
        if booked is not None:
            series = result["series"]
            series["booking"] = route_booking.compare(series["first"], series["later"], series["debt_free"],
                                                     booked, stops, prices, plan["floor_cents"])
            choice = series["booking"]["choice"]
            series["choice"] = "unfunded" if choice is None else {None: "debt_free", 1: "open"}.get(choice["open_stop"], "defer")
    return result


def recurring_plan(first, later, outside, count, plan):
    floor = plan["floor_cents"]
    can_open = all(row is not None and row["payday_cents"] >= floor for row in (first, later))
    can_wait = outside is not None and outside["payday_cents"] >= floor
    base = count * outside["payday_cents"] if can_wait else None
    opened = first["payday_cents"] + (count - 1) * later["payday_cents"] if can_open else None
    choose_loan = can_open and (not can_wait or opened > base)
    threshold = None
    prices = []
    renewal_pct = None
    if can_open and can_wait:
        # Identical replenished stops make each subsequent gain constant. A tie keeps the debt-free plan.
        lead = first["payday_cents"] - outside["payday_cents"]
        gain = later["payday_cents"] - outside["payday_cents"]
        if lead > 0:
            threshold = 1
        elif gain > 0:
            threshold = 2 + (-lead) // gain
            if opened > base:
                # Each extra stop happens only if all previous renewals occurred.
                scale = 100 ** (count - 1)
                renewal_pct = next(p for p in range(101) if lead * scale + gain * sum(
                    p ** i * 100 ** (count - 1 - i) for i in range(1, count)) > 0)
        high = plan["high_price_cents"]
        for price in dict.fromkeys((plan["low_price_cents"], plan["reference_cents"], high)):
            total = mainnet_loan.payday_at(first, high, price) + (count - 1) * mainnet_loan.payday_at(later, high, price)
            prices.append(dict(price_cents=price, open_cents=total, debt_free_cents=count * mainnet_loan.payday_at(outside, high, price)))
    return dict(stops=count, choice="open" if choose_loan else "debt_free" if can_wait else "unfunded",
                open_cents=opened, debt_free_cents=base, break_even_stops=threshold, renewal_pct=renewal_pct, prices=prices,
                first=first, later=later, debt_free=outside, floor_cents=floor)
