import { el, facts, money, request } from "./common.js";

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

async function compare(source) {
  const attempt = ++sequence;
  const split = el("credit-route").value === "reserve";
  const body = { drop: Number(el("credit-drop").value), days: Number(el("credit-days").value), source };
  const button = el("credit-check");
  button.disabled = true;
  el("credit-status").textContent = source === "live" ? "Checking collateral, loan and vendor payment on Devnet…" : "Opening the recorded simulation…";
  try {
    const data = await request(split ? "/api/reserve-credit" : "/api/fuel-credit", body, AbortSignal.timeout(90000));
    if (attempt !== sequence) return;
    if (split) displaySplit(data);
    else display(data);
    el("credit-jump").textContent = split ? "See the " + money(data.borrow_cents) + " loan and " + money(data.reserve_cents) + " reserve payment ↓" : "See the " + money(data.cost_cents) + " financing estimate ↓";
    el("credit-jump").hidden = false;
    const time = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
    el("credit-status").textContent = (source === "live" ? "Public Devnet check" : "Recorded Devnet check") + " · " + time + " UTC";
    if (source === "live" && matchMedia("(max-width: 720px)").matches) el("credit-heading").focus();
  } catch {
    if (attempt !== sequence) return;
    const previous = !el(split ? "split-result" : "credit-result").hidden;
    el("credit-status").textContent = previous ? "Live check unavailable. The previous quote for these terms is shown." : "Quote unavailable. Try checking again.";
  } finally {
    if (attempt === sequence) button.disabled = false;
  }
}
function changeTerms() {
  el("credit-jump").hidden = el("credit-result").hidden = el("split-result").hidden = true;
  el("credit-heading").textContent = "Your payment estimate";
  compare("recorded");
}
el("credit-jump").onclick = () => el("credit-heading").focus();
el("credit-form").onchange = changeTerms;
el("credit-form").onsubmit = event => { event.preventDefault(); compare("live"); };
el("credit-terms").open = matchMedia("(min-width: 761px)").matches;
if (location.hash === "#reserve-first") el("credit-route").value = "reserve";
changeTerms();
