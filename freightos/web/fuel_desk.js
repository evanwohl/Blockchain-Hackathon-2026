import {clearExit, showExit, exitLines} from "./collateral-exit.js";
import {el, facts, money, request, saveFile} from "./common.js";
import {downloadOffer, mixKey, showMix} from "./funding_mix.js";
import {showRun, saveRun} from "./repeat-run.js";
import {showCycle} from "./load_credit.js";

const form = el("desk-form");
let current, controller, timer, revision = 0;
let sizeLoans = true;

function terms() {
  const cents = id => Math.round(el(id).valueAsNumber * 100);
  const menu = el("terms").value;
  const stops = el("borrower-state").value === "new" ? Number(el("stops").value) : 1;
  const booked = stops > 1 && el("booked").value ? {booked: Math.min(stops, Number(el("booked").value))} : {};
  return {...booked, balance: !!booked.booked && sizeLoans, borrower_state: el("borrower-state").value, stops, bill: cents("bill"), invoice: cents("invoice"), stable: cents("stable"), stress: cents("stress"), cash: cents("cash"), budget: cents("budget"),
    target: cents("target"), offer_menu: menu, offer_amount: menu === "counter" ? cents("advance") : null,
    offer_fee: menu === "counter" ? cents("discount") : menu === "partial" ? cents("fee") : 0};
}

function selected() {
  return current?.quote.mainnet.secured_loan.combined[mixKey()];
}

const solAmount = lamports => (lamports / 1e9).toLocaleString("en-US", {maximumFractionDigits: 9}) + " SOL";

function setupLines(loan) {
  const setup = loan.setup;
  const opening = setup.deposit_lamports + setup.setup_fee_lamports;
  return [
    "If you borrow, allow " + solAmount(opening) + " for setup (about " + money(setup.opening_value_cents) + " at the recorded price). This includes " + solAmount(setup.setup_fee_lamports) + " for an extra transaction.",
    "Fully repaying and closing the loan releases " + solAmount(setup.refunded_lamports) + ". " + (setup.returning ? "Your existing profile and collateral-tracking deposits are reused." : "The plan keeps " + solAmount(setup.retained_lamports) + " in the profile and collateral-tracking accounts after payday.") + " It assumes your wallet’s token accounts are ready."
  ];
}

function paint() {
  if (!current) return;
  const {quote, inputs} = current;
  const range = el("desk-stress-range");
  range.hidden = false;
  range.textContent = "Payday prices tested range from " + money(quote.low_price_cents) + " to " + money(quote.high_price_cents)
    + " per SOL." + (quote.high_price_cents > inputs.stress ? " A recorded move exceeds your extra price." : "");
  el("series-panel").hidden = !quote.series;
  if (quote.series) {
    el("mix").hidden = el("desk-liquidity").hidden = true;
    showRun(quote, inputs);
    return;
  }
  showMix(quote);
  const row = selected();
  showExit(row && row.payday_cents >= inputs.target ? [["This stop", row]] : [], quote.low_price_cents, inputs);
  el("plan-heading").textContent = row ? (row.cash_cents ? "Bring " + money(row.cash_cents) + " to the stop." : "Cover fuel without separate cash.") : "Review the funding gap.";
  el("desk-split").hidden = !row;
  if (row) {
    const customer = row.advance_cents - row.surplus_cents;
    const wallet = row.stable_cents + row.sale_cents + row.draw_cents;
    const parts = [];
    if (customer) parts.push(money(customer) + " from early freight pay");
    if (wallet) parts.push(money(wallet) + " from the wallet");
    el("desk-split").textContent = parts.length ? "The plan uses " + parts.join(" and ") + "." : "Your separate cash covers the full fuel bill.";
  }
  const loan = quote.mainnet.secured_loan;
  el("desk-basis").textContent = "Recorded mainnet estimate · " + (loan.setup?.returning ? "Returning borrower" : loan.setup ? "First loan" : "Loan accounts ready");
  el("desk-first-quote").hidden = !!loan.setup || !row?.draw_cents;
  el("desk-setup").hidden = !loan.setup;
  if (loan.setup) {
    el("desk-setup").querySelector("summary").textContent = loan.setup.returning ? "What does reopening my loan need?" : "What does my first loan need?";
    el("desk-repeat").hidden = loan.setup.returning;
    const [upfront, closing] = setupLines(loan);
    const setup = loan.setup;
    el("setup-opening").textContent = (row && !row.draw_cents ? "Your selected plan needs no loan setup. Borrowing would need about " : "Allow about ")
      + money(setup.opening_value_cents) + " upfront for loan setup at the recorded SOL price.";
    facts(el("setup-costs"), [["Returned when the loan closes", money(setup.refund_value_cents)],
      ["New deposits still held after payday", money(setup.retained_value_cents)], ["Setup transaction allowance", setup.setup_fee_value_cents <= 1 ? "About 1¢" : money(setup.setup_fee_value_cents)]]);
    el("setup-return").textContent = upfront + " " + closing;
    const bestLoan = loan.combined.best_loan, sale = loan.combined.most_cash_without_loan;
    el("setup-alternatives").textContent = bestLoan && sale
      ? "At " + money(quote.high_price_cents) + " per SOL, the best plan using a loan leaves " + money(bestLoan.payday_cents)
        + " after restoring balances. Without a loan the best plan leaves " + money(sale.payday_cents) + "."
      : bestLoan ? "Only the loan can fund this stop within your limits." : "No loan-funded plan is available for this stop.";
    if (bestLoan?.all_loans_dominated) el("setup-alternatives").textContent += " For every offer a loan can fund here, selling uses no more SOL and avoids repayment. That holds at any payday SOL price on these terms.";
  }
  const priority = el("mix-priority").value;
  const suffix = el("payday-allow").checked ? "" : "_without_loan";
  const options = quote.mainnet.secured_loan.combined;
  const liquid = options["keep_sol" + suffix], payday = options["most_cash" + suffix];
  const extra = liquid && payday ? liquid.available_sol_value_cents - payday.available_sol_value_cents : 0;
  const shortcut = el("desk-liquidity");
  const missedTarget = priority === "keep_sol" && !liquid && payday;
  shortcut.hidden = priority === "plan" || (!extra && !missedTarget);
  shortcut.textContent = priority === "keep_sol" ? "Compare the highest payday balance" : "Compare keeping " + money(extra) + " more in SOL today";
  el("desk-save").hidden = !row || row.payday_cents < current.inputs.target;
}

function price(delay, focus) {
  clearExit();
  const id = ++revision;
  clearTimeout(timer); controller?.abort();
  current = null;
  el("desk-basis").textContent = "Recorded mainnet estimate";
  el("plan-heading").textContent = "Your payment plan";
  el("desk-split").hidden = el("desk-stress-range").hidden = el("desk-liquidity").hidden = true;
  el("mix").hidden = el("desk-retry").hidden = true;
  const exact = el("terms").value === "counter", flexible = el("terms").value === "partial";
  el("fixed-offer").hidden = el("advance").disabled = el("discount").disabled = !exact;
  el("scaled-offer").hidden = el("fee").disabled = !flexible;
  const inputs = terms();
  el("series-panel").hidden = el("jump-payoff").hidden = el("payoff-check").hidden = true;
  el("booked-field").hidden = inputs.stops < 2;
  const bookings = el("booked");
  if (bookings.options.length !== inputs.stops + 1) {
    bookings.length = 1;
    for (let n = 1; n <= inputs.stops; n++) bookings.add(new Option("Test price changes · " + n + (n === 1 ? " confirmed stop" : " confirmed stops"), String(n)));
    bookings.value = inputs.booked ? String(inputs.booked) : "";
  }
  el("stops-field").hidden = inputs.borrower_state !== "new";
  el("borrower-help").textContent = inputs.borrower_state === "returning"
    ? "Assumes the same profile and collateral-tracking account still exist. Enter only SOL available now."
    : "Use the setup that matches your wallet. The sample has its accounts ready.";
  el("desk-bill").textContent = el("bill").validity.valid ? money(inputs.bill) : "—";
  el("desk-invoice").textContent = el("invoice").validity.valid ? money(inputs.invoice) : "—";
  const exceedsInvoice = exact && inputs.offer_amount + inputs.offer_fee > inputs.invoice;
  el("discount").setCustomValidity(exceedsInvoice ? "Early cash plus the fee exceeds the freight invoice." : "");
  if (!form.checkValidity()) {
    el("desk-status").textContent = exceedsInvoice ? "The early payment and fee together exceed the freight invoice. Change the offer to see your plan."
      : "Complete the funding amounts within the displayed limits to see your plan.";
    el("desk-offer-line").textContent = "Review your entries";
    return;
  }
  el("desk-offer-line").textContent = exact ? money(inputs.offer_amount) + " early · " + money(inputs.offer_fee) + " fee"
    : flexible ? "Up to $99 early" : "Freight paid on day 30";
  el("desk-status").textContent = "Pricing the fuel bill…";
  timer = setTimeout(async () => {
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 45000);
    try {
      const quote = await request("/api/fuel-desk", inputs, active.signal);
      if (revision !== id) return;
      if (!quote.mainnet?.secured_loan?.combined) throw new Error("Missing funding terms");
      current = {quote, inputs};
      el("desk-status").textContent = "";
      paint();
      if (focus) {
        el("plan-heading").focus();
        el("plan-heading").scrollIntoView({block: "start"});
      }
    } catch {
      if (revision !== id) return;
      el("desk-status").textContent = "We couldn’t price this stop. Your entries are still here.";
      el("desk-retry").hidden = false;
    } finally { clearTimeout(timeout); }
  }, delay);
}

function fundingSheet() {
  const row = selected();
  if (!row || row.payday_cents < current.inputs.target) return;
  const {inputs, quote} = current;
  const loan = quote.mainnet.secured_loan;
  const lines = [
    "FreightOS · Driver funding sheet", el("trip-ref").value.trim() || "Single fuel stop", "Recorded mainnet estimate", "",
    "Decision: " + el("mix-priority").selectedOptions[0].text,
    "Secured borrowing: " + (el("payday-allow").checked ? "allowed" : "excluded"),
    "Fuel due today: " + money(quote.bill_cents), "Cash available: " + money(inputs.cash),
    "SOL wallet limit at " + money(quote.reference_cents) + "/SOL: " + money(inputs.budget), "Spendable wallet dollars and reserve: " + money(inputs.stable), "Payday cash target: " + money(inputs.target),
    "Customer offer: " + (inputs.offer_menu === "counter" ? money(inputs.offer_amount) + " early for a " + money(inputs.offer_fee) + " fee"
      : inputs.offer_menu === "partial" ? "Up to $99 early; fee per $99: " + money(inputs.offer_fee) : "No early payment"), "",
    "PLANNED VENDOR PAYMENT", "Cash you bring: " + money(row.cash_cents),
    "Customer early payment applied to fuel: " + money(row.advance_cents - row.surplus_cents),
    "Wallet dollars and fuel reserve: " + money(row.stable_cents), "Sell SOL for: " + money(row.sale_cents),
    "Kamino loan for fuel: " + money(row.draw_cents), "Total planned for vendor: " + money(quote.bill_cents), "",
    "WHEN FREIGHT PAYS · DAY 30", "Gross freight invoice: " + money(inputs.invoice),
    "Request before the stop: " + money(row.advance_cents), "Customer discount: " + money(row.fee_cents),
    "Early cash remaining after fuel: " + money(row.surplus_cents), "Invoice still owed: " + money(row.invoice_balance_cents),
    "SOL locked until repayment: " + (row.pledge_lamports / 1e9).toFixed(4),
    "SOL still available after funding: " + (row.available_lamports / 1e9).toFixed(9) + " SOL (worth " + money(row.available_sol_value_cents) + " at the recorded Kamino price)",
    ...(loan.setup ? [loan.setup.returning ? "Reopening a loan with existing profile and farm accounts." : "First-loan setup included in loan comparisons.", ...setupLines(loan),
      "Setup used by this plan: " + solAmount(row.draw_cents ? loan.setup.deposit_lamports + loan.setup.setup_fee_lamports : 0),
      "Finalized account-deposit observation slot: " + loan.setup.slot] : ["Assumes existing Kamino borrowing accounts."]),
    "Estimated Kamino payoff: " + money(row.repay_cents),
    "After restoring balances at " + money(quote.high_price_cents) + "/SOL: " + money(row.payday_cents), "",
    ...(loan.combined.switch_prices && el("mix-priority").value === "most_cash"
      ? ["With both plans fixed and the loan open until payday, borrowing leaves more at or above " + money(loan.combined.switch_prices.borrowing_from) + "/SOL."] : []),
    "Extra payday price: " + money(inputs.stress) + "/SOL",
    "Gemini scenarios plus extra price: " + money(quote.low_price_cents) + " to " + money(quote.high_price_cents) + "/SOL",
    "Recorded Kamino lending slot: " + loan.slot, "Recorded Orca pool slot: " + quote.mainnet.slot,
    "The estimate assumes freight pays on schedule and uses Gemini price scenarios. Confirm early terms with the customer.",
    "A secured loan can be liquidated before payday. This sheet records a plan. It does not authorize or confirm a payment.", ""
  ];
  lines.push(...exitLines(row));
  saveFile("FreightOS-driver-funding-sheet.txt", lines.join("\n"), "text/plain;charset=utf-8");
}

el("desk-terms").open = matchMedia("(min-width: 761px)").matches;
el("mix-priority").value = "most_cash";
el("mix-priority").onchange = el("payday-choices").onchange = paint;
form.oninput = event => {
  if (event.target.id === "borrower-state") el("stops").value = "1";
  if (event.target.id !== "trip-ref") price(180, false);
};
form.onreset = () => {
  el("exit-keep").value = "100";
  el("cash-exit-budget").value = "30.00";
  sizeLoans = true;
  el("mix-priority").value = "most_cash"; el("payday-allow").checked = true; el("desk-setup").open = false;
  setTimeout(() => price(0, false), 0);
};
form.onsubmit = event => { event.preventDefault(); price(0, true); };
el("desk-liquidity").onclick = () => {
  el("mix-priority").value = el("mix-priority").value === "keep_sol" ? "most_cash" : "keep_sol";
  paint();
  el("mix-priority").focus();
};
el("desk-first-quote").onclick = () => {
  el("borrower-state").value = "new";
  el("stops").value = "1";
  el("desk-setup").open = true;
  price(0, true);
};
el("desk-repeat").onclick = () => { el("stops").value = "4"; price(0, true); };
el("size-route").onclick = () => { sizeLoans = !sizeLoans; price(0, true); };
el("series-single").onclick = () => { el("stops").value = "1"; price(0, true); };
el("series-save").onclick = () => { if (current?.quote.series) saveRun(current.quote, el("trip-ref").value.trim()); };
el("desk-retry").onclick = () => price(0, false);
el("desk-save").onclick = fundingSheet;
el("mix-request").onclick = () => downloadOffer(selected(), el("trip-ref").value.trim());
el("cycle-verify").onclick = () => showCycle(true);
const example = new URLSearchParams(location.search).get("example");
if (["repeat-run", "booked-route", "balanced-route"].includes(example)) {
  el("bill").value = "186"; el("borrower-state").value = "new"; el("stops").value = "4";
}
if (["booked-route", "balanced-route"].includes(example)) {
  sizeLoans = example === "balanced-route";
  for (const [id, value] of Object.entries({bill: 745, invoice: 3100, cash: 250, budget: 600, stable: 150, target: 2000, advance: 250, discount: 5}))
    el(id).value = value;
  el("booked").add(new Option("2 confirmed stops", "2", false, true));
}
showCycle(false);
price(0, false);
