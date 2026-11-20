import { el, facts, money, request, saveFile, tokenUnits } from "./common.js";

import { clearSale, setupSale } from "./fuel_swap.js";
import { displayCustomer } from "./customer_credit.js";

let sequence = 0;
const sol = lamports => (lamports / 1e9).toLocaleString("en-US", { maximumFractionDigits: 9 }) + " SOL";
const pct = bps => (bps / 100).toFixed(2) + "%";

function display(d) {
  el("credit-heading").textContent = "What borrowing would cost";
  el("credit-cost").textContent = money(d.cost_cents);
  el("credit-repay").textContent = "Repay about " + money(d.repay_cents) + " from the $200 invoice in " + d.days + " days.";
  const room = d.fee_headroom_cents;
  facts(el("credit-compare"), [["Fee to collect the invoice early", "$10.00"],
    [room >= 0 ? "Room for other costs before early pay costs less" : "Loan cost above early pay", money(Math.abs(room))],
    ["SOL pledged at full bill scale", sol(d.collateral_lamports * 100) + " (about " + money(Math.round(d.collateral_lamports / 1e9 * 100 * Number(d.sol_price) * 100)) + ")"]]);
  el("credit-pledge").textContent = "At a " + d.drop + "% price drop, the loan would use " + pct(d.stress_ltv_bps) + " of the collateral’s value by payday.";
  facts(el("credit-risk"), [["SOL price in the check", "$" + d.sol_price], ["Liquidation price by payday", money(d.liquidation_price_cents)]]);
  el("credit-proof").textContent = "At slot " + d.slot.toLocaleString("en-US") + ", Kamino accepted " + sol(d.collateral_lamports)
    + " of test SOL, opened a debt and paid 1.90 test USDC to the named demo vendor in one simulation. One test dollar represents $100 on this bill.";
  el("credit-assumptions").textContent = "The estimate holds the reserve’s " + pct(d.rate_bps) + " configured annual borrowing rate and USDC price constant. "
    + "The pledge leaves 10 percentage points of room below liquidation after the chosen drop. The draw included a "
    + sol(d.fee_lamports) + " network fee and used " + sol(d.setup_lamports) + " for account setup. Setup, network, repayment transaction and conversion costs are excluded from the modeled financing cost.";
  el("credit-result").hidden = false;
}

function checkPledge(d) {
  const input = el("pledge-limit");
  const output = el("pledge-fit");
  if (input.validity.badInput || !input.validity.valid) {
    output.textContent = "Enter 0 to 1,000 SOL with up to three decimal places.";
  } else if (!input.value) {
    output.textContent = "Compare both loans at the selected payment date and price drop. Keep setup cash available separately.";
  } else {
    const limit = Math.round(input.valueAsNumber * 1000);
    const splitFits = limit >= d.split_pledge_millis;
    const fullFits = limit >= d.full_pledge_millis;
    if (splitFits && fullFits) output.textContent = "Both loans fit this pledge limit.";
    else if (splitFits) output.textContent = "Only the reserve-first plan fits. It covers the whole bill within your pledge limit.";
    else if (fullFits) output.textContent = "Only borrowing the whole bill fits this pledge limit.";
    else output.textContent = "Neither checked loan fits. Early payment on the $200 invoice would still cover $190 for a $10 fee.";
    output.textContent += " Loan setup cash is separate from your pledge.";
  }
}

function displaySplit(d) {
  el("credit-heading").textContent = "Pay the $190 fuel bill";
  el("split-borrow").textContent = money(d.borrow_cents);
  el("split-reserve-bar").style.width = (100 * d.reserve_cents / d.bill_cents) + "%";
  el("split-payment").textContent = money(d.reserve_cents) + " from the reserve covers the rest. The vendor receives " + money(d.bill_cents) + " across two payments.";
  facts(el("split-facts"), [
    ["Repay when the invoice clears in " + d.days + " days", money(d.projected_repay_cents)],
    ["Left from the " + money(d.invoice_cents) + " invoice after repayment and setting " + money(d.reserve_cents) + " back aside", money(d.invoice_cents - d.projected_repay_cents - d.reserve_cents)],
    ["Cash for loan setup and its network fee", money(d.entry_cash_cents)],
    ["SOL pledged for the smaller loan", (d.split_pledge_millis / 1000).toLocaleString("en-US") + " SOL"]
  ]);
  el("split-next").textContent = money(d.keep_cents) + " stays available from the vault. That covers three gallons at the sample diesel price if oil reaches $" + d.holdback.threshold + ", using the recorded Gemini market and the trip’s fuel assumptions.";
  el("split-tradeoff").textContent = d.saved_cents > 0
    ? "Want to leave the extra " + money(d.reserve_cents) + " in your reserve? Borrowing the full bill adds about " + money(d.saved_cents) + " in estimated costs."
    : "At these terms, borrowing the full bill keeps more fuel money in reserve for the same or lower estimated cost.";
  facts(el("split-costs"), [
    ["Payday repayment on the full loan", money(d.full_repay_cents)],
    ["Cost of borrowing the whole bill", money(d.baseline_cost_cents)],
    ["Use spare reserve money first", money(d.split_cost_cents)],
    [d.saved_cents < 0 ? "Extra estimated cost using the reserve" : "Estimated savings using the reserve", money(Math.abs(d.saved_cents))],
    ["Included for missed reserve earnings", money(d.missed_yield_cents)]
  ]);
  const limit = d.max_yield_bps === null ? "The split has no lower-cost yield range." : "At these loan terms the split costs less up to " + pct(d.max_yield_bps) + " annual vault yield, tested in whole basis points.";
  el("split-pricing").textContent = "Estimates include interest, loan fees, opening network fees and missed vault earnings at " + (Number(d.yield.apy) * 100).toFixed(2) + "% annual yield. " + limit + " Both loans need the same account setup. Conversion and payoff fees are excluded.";
  facts(el("split-rate-check"), [
    ["Annual rate after the full draw in the test pool", pct(d.full_rate_bps)],
    ["Annual rate after the smaller draw", pct(d.split_rate_bps)],
    [d.same_rate_saved_cents >= 0 ? "Savings if both loans had the full draw’s rate" : "Extra cost if both loans had the full draw’s rate", money(Math.abs(d.same_rate_saved_cents))]
  ]);
  el("split-risk").textContent = "Borrowing the whole bill would pledge " + (d.full_pledge_millis / 1000).toLocaleString("en-US") + " SOL. Both loans allow for a " + d.drop + "% price drop. A larger fall can put your SOL at risk of liquidation. You still owe the loan if the customer pays late.";
  el("split-proof").textContent = "An unsigned vault withdrawal paid the demo vendor and a second unsigned loan covered the gap. If only the first payment goes through, " + money(d.borrow_cents) + " remains due. The checks used Devnet slots " + d.reserve_slot.toLocaleString("en-US") + " and " + d.loan_slot.toLocaleString("en-US") + ". One test dollar represents $100 of fuel. A full-size loan could have different terms.";
  checkPledge(d);
  el("pledge-limit").oninput = () => checkPledge(d);
  el("split-result").hidden = false;
}

function showPartial(d) {
  const offer = d.offer, p = offer.partial;
  el("credit-heading").textContent = "Advance part of the invoice";
  el("combined-label").textContent = "Request early from the $200 invoice";
  el("combined-total").textContent = money(p.gross_cents);
  el("combined-sources").textContent = "The customer keeps " + money(p.discount_cents) + " and sends " + money(p.advance_cents)
    + (p.draw_cents ? ". Use " + money(p.draw_cents) + " of spare reserve money to finish paying for fuel." : ". This covers the fuel bill.");
  el("combined-bar").parentElement.hidden = !p.draw_cents;
  el("combined-bar").style.width = 100 * p.draw_cents / d.bill_cents + "%";
  facts(el("combined-facts"), [["Fuel bill covered on the planned date", money(d.bill_cents)],
    ["Still owed by customer on the regular date", money(p.regular_cents)], ["Put back in the reserve", money(p.draw_cents)],
    ["Cash left after refill and expenses", money(p.payday_cash_cents)]]);
  el("combined-choice").textContent = "What the advance costs";
  el("combined-comparison").textContent = money(p.discount_cents) + " customer fee + " + money(p.missed_yield_cents) + " estimated missed reserve earnings + "
    + money(p.expense_cents) + " entered reserve expenses = " + money(p.cost_cents) + ".";
  el("combined-payday").textContent = "The whole-invoice fee is " + money(offer.fee_cents) + (offer.shortfall_cents ? " and still leaves " + money(offer.shortfall_cents) + " of fuel unpaid." : ".")
    + " The loan plan " + (offer.payday_cash_cents < 0 ? "is short " + money(-offer.payday_cash_cents) + " at payday." : "costs an estimated " + money(offer.loan_cost_cents) + ".");
  el("combined-failure-title").textContent = "Confirm before paying for fuel";
  el("combined-failure").textContent = "Confirm the partial advance, fee and both payment dates with the customer. Wait for the advance to clear. No SOL collateral is needed.";
  el("combined-terms").textContent = "Reserve use is capped so at least " + money(d.holdback.cost_cents) + " remains budgeted for three more gallons under the recorded Gemini oil scenario.";
  el("combined-risk").textContent = "Estimated missed earnings use the recorded vault yield through the selected refill date. Network expenses use the amount entered. Check the exact withdrawal before using reserve money.";
  el("partial-status").textContent = "";
  el("partial-evidence").hidden = true;
  const saved = el("partial-saved"), live = el("partial-live");
  saved.hidden = !p.recorded; live.hidden = !p.draw_cents; live.disabled = saved.disabled = false;
  el("partial-request").onclick = () => saveFile("FreightOS-advance-request.txt",
    "Freight invoice [invoice or load ID]\n\nPlease confirm a partial early payment under your " + money(offer.fee_cents) + " offer for the full $200 invoice. "
    + "We request " + money(p.gross_cents) + " early, with a fee of " + money(p.discount_cents) + ". Please confirm that "
    + money(p.advance_cents) + " will clear by [fuel purchase date] and that the remaining " + money(p.regular_cents)
    + " will be paid in full on [regular payment date].\n", "text/plain");
  let previousCheck = "";
  async function check(source) {
    const revision = sequence;
    live.disabled = saved.disabled = true;
    el("partial-status").textContent = "Checking the vendor transfer and what stays in the vault…";
    try {
      const q = await request("/api/advance-reserve", {draw_cents: p.draw_cents, source}, AbortSignal.timeout(90000));
      if (revision !== sequence) return;
      el("partial-status").textContent = (source === "live" ? "Public Devnet simulation" : "Recorded Devnet simulation") + " · slot " + q.slot.toLocaleString("en-US")
        + ". The vendor received " + tokenUnits(q.vendor_raw) + " test USDC (" + money(p.draw_cents) + " at sample scale). The remaining shares could return "
        + money(Math.floor(q.retained_raw / q.raw_units_per_cent)) + ". The customer advance is arranged separately.";
      previousCheck = el("partial-status").textContent;
      el("partial-evidence").hidden = source !== "recorded";
    } catch (err) {
      if (revision === sequence) el("partial-status").textContent = "New reserve check unconfirmed. " + err.message + (previousCheck ? " Previous check: " + previousCheck : "");
    } finally { if (revision === sequence) live.disabled = saved.disabled = false; }
  }
  saved.onclick = () => check("recorded"); live.onclick = () => check("live");
}

function displayCombined(d) {
  const reserve = money(d.reserve_raw / d.scale), debt = money(d.borrow_raw / d.scale);
  const stress = d.payday_stress, offer = d.offer;
  el("credit-intro").textContent = "Your customer offers to pay a $200 freight invoice early for " + money(offer.fee_cents) + ". Compare that with using fuel reserves and borrowing against SOL.";
  const target = new URLSearchParams({days: d.days, drop: d.drop, late: Number(stress.protected), fee: offer.fee_cents, other: offer.other_cents});
  if (offer.partial_allowed) { target.set("partial", "1"); target.set("reservefee", offer.reserve_fee_cents); }
  el("combined-wallet-link").href = "/load#combined?" + target;
  el("combined-wallet-link").hidden = offer.choice !== "loan";
  el("combined-download").href = stress.protected ? "/evidence/atomic-late" : "/evidence/atomic-fuel";
  el("combined-total").textContent = money(d.bill_cents);
  el("combined-sources").textContent = reserve + " from your reserve and " + debt + " borrowed against SOL. Both sources pay the vendor together.";
  el("combined-bar").style.width = (100 * d.reserve_raw / d.vendor_raw) + "%";
  facts(el("combined-facts"), [["Still due on fuel if the payment succeeds", money(0)],
    ["Kept in reserve for three more gallons", money(d.retained_raw / d.scale)],
    ["Estimated loan payoff in " + d.days + " days", money(d.repay_cents)]]);
  el("combined-failure").textContent = "If either source fails, your reserve stays put, no debt opens and you still owe the full bill. A submitted attempt can still cost a network fee.";
  el("combined-terms").textContent = "At demo scale the pledge is " + sol(d.pledge_lamports) + " on Devnet. The payment needs "
    + sol(d.setup_lamports) + " for loan account setup and " + sol(d.temporary_lamports) + " for temporary accounts that close during payment. Its network fee is " + sol(d.fee_lamports) + ".";
  const early = offer.choice === "early", short = offer.choice === "short";
  const choice = early ? "Take the early freight payment" : short ? "Both plans leave a gap" : "Use the reserve and loan";
  el("credit-heading").textContent = choice;
  el("combined-choice").textContent = "What the two options cost";
  el("combined-failure-title").textContent = "If the money falls short";
  if (early || short) {
    el("combined-label").textContent = early ? "Cash from the early invoice" : "Fuel still due with early pay";
    el("combined-total").textContent = money(early ? offer.early_net_cents : offer.shortfall_cents);
    el("combined-sources").textContent = early ? "Confirm the early date with your customer, then pay the vendor from the invoice proceeds."
      : "The early invoice does not cover all the fuel. The loan would leave too little cash on payday.";
    facts(el("combined-facts"), early ? [["Pay the vendor", money(d.bill_cents)], ["Cash left after fuel", money(offer.early_net_cents - d.bill_cents)], ["SOL to pledge", "None"]]
      : [["Fuel-day gap with early pay", money(offer.shortfall_cents)], ["Payday gap with the loan", money(-offer.payday_cash_cents)]]);
    el("combined-failure-title").textContent = early ? "Before you rely on the payment" : "What needs to change";
    el("combined-failure").textContent = early ? "The offer assumes cleared cash by the fuel stop. The customer still needs to confirm those terms."
      : "Ask for a lower early-pay fee or identify cash that can cover the shortfall.";
    el("combined-choice").textContent = "How the loan compares";
  } else {
    el("combined-label").textContent = "Fuel vendor would receive";
  }
  el("combined-bar").parentElement.hidden = early || short;
  const costs = "Loan costs of " + money(offer.loan_cost_cents) + " include interest, missed reserve earnings and the expenses entered. Early pay costs " + money(offer.fee_cents) + ".";
  const limit = offer.payday_cash_cents < 0 ? "The loan is also short " + money(-offer.payday_cash_cents) + " after repayment and reserve refill."
    : "After loan payoff, reserve refill and cash expenses, the invoice leaves " + money(offer.payday_cash_cents) + ".";
  el("combined-comparison").textContent = costs + " " + limit + " Compared with the loan, the highest fee to take the whole invoice early is " + money(offer.early_fee_ceiling_cents) + ".";
  el("combined-payday").textContent = stress.protected
    ? "If freight pays on day " + stress.days + " after borrowing and the rate doubles, estimated payoff is " + money(stress.repay_cents) + ". The loan plan pledges "
      + sol(d.pledge_lamports - stress.ordinary_pledge_lamports) + " extra in test collateral to keep its safety margin."
    : "If the check takes another week and the borrowing rate doubles, estimated repayment rises to " + money(stress.repay_cents) + ".";
  el("combined-terms").textContent += " Of the setup deposit, " + sol(d.obligation_rent_lamports) + " funds the loan account and "
    + sol(d.profile_rent_lamports) + " funds the borrower profile. Closing the loan releases the loan account deposit.";
  el("combined-risk").textContent = "The loan is sized for a " + d.drop + "% SOL drop. Falling collateral can be liquidated. The "
    + money(d.interest_cents) + " projected borrowing cost assumes today’s rate through payday. The three-gallon target uses the recorded Gemini $" + d.holdback.threshold + " oil scenario.";
  el("combined-proof").textContent = "Unsigned public Devnet simulation at slot " + d.slot.toLocaleString("en-US") + ". The vendor received "
    + (d.vendor_raw / 1e6).toFixed(6) + " test USDC in one transfer. A second simulation tried to pay one token unit more than the available proceeds. All token balances stayed unchanged. A third simulation redeemed the remaining shares.";
  const partial = offer.choice === "partial";
  el("partial-request").hidden = el("partial-actions").hidden = !partial;
  el("combined-loan-proof").hidden = partial;
  if (partial) showPartial(d);
  else if (offer.partial) el("combined-comparison").textContent += " Advancing part would cost " + money(offer.partial.cost_cents) + ".";
  if (offer.partial_allowed && offer.partial_fee_limit_cents !== null) {
    el("combined-comparison").textContent += " With partial payment allowed, the highest full-invoice fee that still "
      + (offer.payday_cash_cents < 0 ? "covers fuel and the refill" : "covers fuel and the refill at or below the loan cost")
      + " is " + money(offer.partial_fee_limit_cents) + ".";
  }
  el("combined-result").hidden = false;
}

async function compare(source) {
  const attempt = ++sequence;
  const route = el("credit-route").value;
  const directSale = route === "sale", customer = directSale || route === "customer";
  const split = route === "reserve", combined = route === "combined";
  const body = { drop: Number(el("credit-drop").value), days: Number(el("credit-days").value), source };
  el("combined-settings").hidden = !combined;
  const form = el("credit-form"), jump = el("credit-jump");
  if (customer && form.previousElementSibling !== jump) form.before(jump);
  else if (!customer && form.nextElementSibling !== jump) form.after(jump);
  el("customer-settings").hidden = !customer;
  el("credit-terms").hidden = customer;
  el("sale-primary").hidden = !directSale;
  const container = el(directSale ? "sale-primary" : "sale-inline");
  if (el("fuel-sale").parentElement !== container) { container.append(el("fuel-sale")); el("fuel-sale").open = directSale; }
  el("customer-settings").querySelector("button").textContent = directSale ? "Refresh prices on public Solana" : "Refresh loan checks on public Solana";
  for (const input of el("customer-settings").querySelectorAll("input, select, button")) input.disabled = !customer;
  if (directSale) el("credit-intro").textContent = "Use the demo wallet’s spendable cash and compare two exchanges for the rest. See what reaches the fuel company, what stays for the next stop, and how much borrowing would take.";
  else if (customer) el("credit-intro").textContent = "Fuel is due before your $200 invoice clears. With no spare spending cash, see how an early customer payment and your reserve can reduce the loan.";
  else if (!combined) el("credit-intro").textContent = "Taking the $200 invoice early costs $10 in this example. Compare it with borrowing against SOL to pay for fuel.";
  const partial = combined && el("allow-partial").checked;
  el("partial-expense-field").hidden = el("partial-expense").disabled = !partial;
  for (const id of ["offer-fee", "offer-other", "protect-payday", "allow-partial"]) el(id).disabled = !combined;
  if (combined) {
    el("credit-check").disabled = false;
    if (!el("credit-form").reportValidity()) { el("credit-status").textContent = "Check the offer amounts."; return; }
    Object.assign(body, {partial, reserve_fee_cents: partial ? Math.round(el("partial-expense").valueAsNumber * 100) : 0, protect_payday: el("protect-payday").checked,
      offer_fee_cents: Math.round(el("offer-fee").valueAsNumber * 100), other_cents: Math.round(el("offer-other").valueAsNumber * 100)});
  }
  if (customer) {
    if (!el("credit-form").reportValidity()) { el("credit-status").textContent = "Check the wallet amount and fees."; return; }
    Object.assign(body, {menu: el("customer-menu").value, fee_cents: Math.round(el("customer-fee").valueAsNumber * 100),
      wallet_lamports: Math.round(el("customer-wallet").valueAsNumber * 1e9), extra_cents: Math.round(el("customer-extra").valueAsNumber * 100)});
  }
  if (directSale) {
    const offers = {none: [0], half: [0, 4950], full: [0, 9900], both: [0, 4950, 9900]}[body.menu];
    el("credit-status").textContent = "Demo wallet · one test cent represents one dollar of fuel.";
    if (el("sale-result").hidden) el("credit-heading").textContent = "Checking the fuel payment";
    await setupSale({options: offers.map(advance_cents => ({advance_cents})), choice: Number(el("sale-customer").value)}, body, true);
    return;
  }
  const button = customer ? el("customer-settings").querySelector("button") : el("credit-check");
  button.disabled = true;
  el("credit-status").textContent = source === "live" ? "Checking collateral, loan and vendor payment on Devnet…" : "Opening the recorded simulation…";
  try {
    const data = await request(customer ? "/api/customer-credit" : combined ? "/api/atomic-fuel" : split ? "/api/reserve-credit" : "/api/fuel-credit", body, AbortSignal.timeout(90000));
    if (attempt !== sequence) return;
    if (customer) { displayCustomer(data); setupSale(data, body); }
    else if (combined) displayCombined(data);
    else if (split) displaySplit(data);
    else display(data);
    el("credit-jump").textContent = customer ? el("credit-heading").textContent + " · see breakdown ↓" : combined ? "See the funding decision ↓" : split ? "See the " + money(data.borrow_cents) + " loan and " + money(data.reserve_cents) + " reserve payment ↓" : "See the " + money(data.cost_cents) + " financing estimate ↓";
    el("credit-jump").hidden = false;
    const time = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
    el("credit-status").textContent = (source === "live" ? "Public Devnet check" : "Recorded Devnet check") + (customer ? " · remaining fuel payment" : combined ? " · slot " + data.slot.toLocaleString("en-US") : " · " + time + " UTC");
    if (source === "live" && matchMedia("(max-width: 720px)").matches) el("credit-heading").focus();
  } catch {
    if (attempt !== sequence) return;
    const previous = !el(customer ? "customer-result" : combined ? "combined-result" : split ? "split-result" : "credit-result").hidden;
    el("credit-status").textContent = previous ? "Live check unavailable. The previous quote for these terms is shown." : "Quote unavailable. Try checking again.";
  } finally {
    if (attempt === sequence) button.disabled = false;
  }
}
function changeTerms() {
  clearSale();
  el("customer-result").hidden = true;
  el("credit-jump").hidden = el("combined-result").hidden = el("credit-result").hidden = el("split-result").hidden = true;
  el("credit-heading").textContent = "Your payment estimate";
  compare("recorded");
}
el("credit-jump").onclick = () => el("credit-heading").focus();
el("customer-settings").oninput = event => {
  if (event.target.type !== "number") return;
  ++sequence;
  clearSale();
  el("customer-result").hidden = el("credit-jump").hidden = true;
  el("credit-heading").textContent = "Your payment estimate";
  el("customer-settings").querySelector("button").disabled = false;
  el("credit-status").textContent = "Finish editing to compare these terms.";
};
el("credit-form").onchange = changeTerms;
el("credit-form").onsubmit = event => { event.preventDefault(); compare("live"); };
el("credit-terms").open = matchMedia("(min-width: 761px)").matches;
el("credit-route").value = ["#customer-contribution", "#pay-without-loan"].includes(location.hash) ? "customer" : location.hash === "#one-payment" ? "combined" : location.hash === "#reserve-first" ? "reserve" : "loan";
if (location.hash === "#partial-advance") {
  el("credit-route").value = "combined";
  el("combined-settings").querySelector("details").open = true;
  el("allow-partial").checked = el("protect-payday").checked = true;
  el("offer-fee").value = "3.00";
}
if (location.hash === "#pay-without-loan") {
  el("credit-route").value = "sale";
  el("customer-menu").value = "none";
  el("customer-wallet").value = "0.020";
  el("fuel-sale").open = true;
}
changeTerms();
