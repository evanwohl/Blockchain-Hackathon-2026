import { el, money, request } from "./common.js";

let state;

function displayQuote() {
  const profile = el("quote-profile").value;
  const scenario = el("quote-scenario").value;
  const plan = state.funding_plans[profile][scenario];
  const locked = state.review || state.receipt || state.manual_payment;
  const source = { "quick pay": "You pay from early invoice pay", "credit line": "You pay with bank credit", "cash on hand": "You pay with cash" };
  const due = plan.calendar.bills[0].day;
  const paid = plan.invoice_elections.find(item => item.invoice === "primary load").day;
  el("window-due").textContent = "Day " + due;
  el("window-paid").textContent = "Day " + paid;
  el("window-gap").textContent = Math.abs(paid - due) + "-day gap";
  el("cash-window").hidden = false;
  el("quote-load").textContent = plan.load;
  el("quote-bill").textContent = money(plan.bill_cents);
  el("quote-reserve").textContent = money(plan.payment_cents);
  el("quote-reserve").parentElement.hidden = !plan.payment_cents;
  el("quote-outside").textContent = money(plan.other_funds_cents);
  el("outside-source").textContent = source[profile];
  el("quote-result").style.setProperty("--reserve-share", (100 * plan.payment_cents / plan.bill_cents) + "%");
  el("quote-fees").textContent = money(plan.cost_cents) + " estimated fees";
  const alternative = profile === "credit line" ? "Bank credit alone" : "Early pay on the main invoice";
  el("quote-compare").textContent = plan.savings_cents
    ? alternative + " costs " + money(plan.alternative_cost_cents) + ". Estimated savings of " + money(plan.savings_cents) + "."
    : "Available cash covers the bill with no financing fee.";
  el("quote-open").href = locked ? "/bill" : "/bill#" + new URLSearchParams({ plan: profile, fuel: scenario });
  el("quote-open").textContent = locked ? "Return to current bill" : "Review this bill";
  el("quote-status").textContent = locked ? "Your bill already has a payment record or pending approval. These comparisons are previews." : "";
}

async function load() {
  el("quote-retry").hidden = true;
  el("quote-status").textContent = "Loading plan prices…";
  try {
    state = await request("/api/state");
    displayQuote();
    el("quote-inputs").disabled = false;
    el("quote-result").hidden = false;
  } catch {
    el("quote-status").textContent = "Plan prices are unavailable. Try loading them again.";
    el("quote-retry").hidden = false;
  }
}
el("quote-inputs").onchange = displayQuote;
el("quote-retry").onclick = load;
load();
