import {el, money, request, saveFile} from "./common.js";
import {downloadOffer, mixKey, showMix} from "./funding_mix.js";
import {showCycle} from "./load_credit.js";

const form = el("desk-form");
let current, controller, timer, revision = 0;

function terms() {
  const cents = id => Math.round(el(id).valueAsNumber * 100);
  const menu = el("terms").value;
  return {source: "recorded", received: 20000, stress: 15000, cash: cents("cash"), budget: cents("budget"),
    target: cents("target"), offer_menu: menu, offer_amount: menu === "counter" ? cents("advance") : null,
    offer_fee: menu === "counter" ? cents("discount") : menu === "partial" ? cents("fee") : 0};
}

function selected() {
  return current?.quote.mainnet.secured_loan.combined[mixKey()];
}

function paint() {
  if (!current) return;
  showMix(current.quote);
  const row = selected();
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
  el("desk-save").hidden = !row || row.payday_cents < current.inputs.target;
}

function price(delay, focus) {
  const id = ++revision;
  clearTimeout(timer); controller?.abort();
  current = null;
  el("plan-heading").textContent = "Your payment plan";
  el("desk-split").hidden = true;
  el("mix").hidden = el("desk-retry").hidden = true;
  const exact = el("terms").value === "counter", flexible = el("terms").value === "partial";
  el("fixed-offer").hidden = el("advance").disabled = el("discount").disabled = !exact;
  el("scaled-offer").hidden = el("fee").disabled = !flexible;
  if (!form.checkValidity()) {
    el("desk-status").textContent = "Complete the funding amounts to see your plan.";
    el("desk-offer-line").textContent = "Review your entries";
    return;
  }
  const inputs = terms();
  el("desk-offer-line").textContent = exact ? money(inputs.offer_amount) + " early · " + money(inputs.offer_fee) + " fee"
    : flexible ? "Up to $99 early" : "Freight paid on day 30";
  el("desk-status").textContent = "Pricing the fuel bill…";
  timer = setTimeout(async () => {
    const active = new AbortController(); controller = active;
    const timeout = setTimeout(() => active.abort(), 20000);
    try {
      const quote = await request("/api/fuel-stop", inputs, active.signal);
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
    "FreightOS · FOS-2048 · Driver funding sheet", "Sample fuel stop · Recorded mainnet estimate", "",
    "Decision: " + el("mix-priority").selectedOptions[0].text,
    "Secured borrowing: " + (el("payday-allow").checked ? "allowed" : "excluded"),
    "Fuel due today: " + money(quote.bill_cents), "Cash available: " + money(inputs.cash),
    "SOL wallet limit at " + money(quote.reference_cents) + "/SOL: " + money(inputs.budget), "Payday cash target: " + money(inputs.target),
    "Customer offer: " + (inputs.offer_menu === "counter" ? money(inputs.offer_amount) + " early for a " + money(inputs.offer_fee) + " fee"
      : inputs.offer_menu === "partial" ? "Up to $99 early; fee per $99: " + money(inputs.offer_fee) : "No early payment"), "",
    "PLANNED VENDOR PAYMENT", "Cash you bring: " + money(row.cash_cents),
    "Customer early payment applied to fuel: " + money(row.advance_cents - row.surplus_cents),
    "Wallet dollars and fuel reserve: " + money(row.stable_cents), "Sell SOL for: " + money(row.sale_cents),
    "Kamino loan for fuel: " + money(row.draw_cents), "Total planned for vendor: " + money(quote.bill_cents), "",
    "WHEN FREIGHT PAYS · DAY 30", "Gross freight invoice: " + money(inputs.received),
    "Request before the stop: " + money(row.advance_cents), "Customer discount: " + money(row.fee_cents),
    "Early cash remaining after fuel: " + money(row.surplus_cents), "Invoice still owed: " + money(row.invoice_balance_cents),
    "SOL locked until repayment: " + (row.pledge_lamports / 1e9).toFixed(4),
    "Estimated Kamino payoff: " + money(row.repay_cents),
    "After restoring balances at " + money(quote.high_price_cents) + "/SOL: " + money(row.payday_cents), "",
    "Recorded Kamino lending slot: " + loan.slot, "Recorded Orca pool slot: " + quote.mainnet.slot,
    "The estimate assumes freight pays on schedule and uses Gemini price scenarios. Confirm early terms with the customer.",
    "A secured loan can be liquidated before payday. This sheet records a plan. It does not authorize or confirm a payment.", ""
  ];
  saveFile("FOS-2048-driver-funding-sheet.txt", lines.join("\n"), "text/plain;charset=utf-8");
}

el("desk-terms").open = matchMedia("(min-width: 761px)").matches;
el("mix-priority").value = "most_cash";
el("mix-priority").onchange = el("payday-choices").onchange = paint;
form.oninput = () => price(180, false);
form.onsubmit = event => { event.preventDefault(); price(0, true); };
el("desk-retry").onclick = () => price(0, false);
el("desk-save").onclick = fundingSheet;
el("mix-request").onclick = () => downloadOffer(selected());
el("cycle-verify").onclick = () => showCycle(true);
showCycle(false);
price(0, false);
