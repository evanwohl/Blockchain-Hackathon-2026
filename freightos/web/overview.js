import { drawFunding } from "./payment_slips.js";
import { cashPriority, el, money, oilOptions, request, usesCredit, verifySample } from "./common.js";

let state;

function displayQuote() {
  const scenario = el("quote-scenario").value;
  const profile = cashPriority(state, el("quote-profile").value, scenario);
  const plan = state.funding_plans[profile][scenario];
  const locked = state.review || state.receipt || state.manual_payment;
  const source = { "quick pay": "You pay from the return-load invoice", "credit line": "You pay with bank credit", "cash on hand": "You pay directly with cash" };
  const due = plan.calendar.bills[0].day;
  const paid = plan.invoice_elections.find(item => item.invoice === "primary load").day;
  const main = plan.calendar.invoices.find(item => item.id === "primary load");
  el("window-due").textContent = "Day " + due;
  el("window-paid").textContent = "Day " + paid;
  el("window-gap").textContent = Math.abs(paid - due) + "-day gap";

  el("quote-load").textContent = plan.load;
  const [dollars, cents] = money(plan.bill_cents).split(".");
  const fraction = document.createElement("span");
  fraction.className = "balance-cents";
  fraction.textContent = "." + cents;
  el("quote-bill").replaceChildren(dollars, fraction);
  el("quote-reserve").textContent = money(plan.payment_cents);
  el("quote-reserve").parentElement.hidden = !plan.payment_cents;
  el("quote-outside").textContent = money(plan.other_funds_cents);
  el("outside-source").textContent = source[usesCredit(profile) ? "credit line" : profile];
  el("quote-result").style.setProperty("--reserve-share", (100 * plan.payment_cents / plan.bill_cents) + "%");
  el("quote-fees").textContent = money(plan.cost_cents);
  el("compare-cell").hidden = profile === "cash on hand";
  el("compare-name").textContent = usesCredit(profile) ? "Bank credit alone" : "Early pay on main invoice";
  el("compare-price").textContent = money(plan.alternative_cost_cents);
  el("quote-compare").textContent = plan.savings_cents
    ? "You keep an estimated " + money(plan.savings_cents) + " more of this load’s earnings."
    : "Available cash covers the bill with no financing fee.";
  el("quote-open").href = locked ? "/bill" : "/bill#" + new URLSearchParams({ plan: profile, fuel: scenario });
  el("quote-open").textContent = locked ? "Return to current bill" : "Walk through this bill";
  el("offer-example").hidden = locked || profile !== "quick pay";
  el("road-example").hidden = el("offer-example").hidden;
  el("offer-example").href = "/bill#" + new URLSearchParams({ plan: profile, fuel: scenario, view: "surcharge-offer" });
  el("delay-link").hidden = locked || !state.pairing_values[profile][scenario].invoice_delay;
  el("delay-link").href = "/bill#" + new URLSearchParams({ plan: profile, fuel: scenario, view: "late-freight" });
  el("quote-scope").textContent = plan.payment_cents ? "Practice bill · Test funds on a local Solana fork" : "Practice bill · You record the direct payment";
  const original = state.plans["fuel budget"].bill_cents;
  el("flow-load").textContent = "Sample load " + plan.load;
  el("flow-plan").href = el("quote-open").href;
  el("flow-fuel-value").textContent = money(plan.bill_cents);
  el("flow-original").textContent = money(original);
  el("flow-gallons").textContent = plan.gemini.gallons + " gallons";
  el("flow-fees").textContent = money(plan.cost_cents);
  el("flow-reserve").textContent = money(plan.payment_cents);
  el("flow-outside").textContent = money(plan.other_funds_cents);
  el("flow-payment-method").textContent = plan.payment_cents ? "Solana" : "Direct";
  el("flow-pay-label").textContent = plan.payment_cents ? "To approve from reserve" : "Driver pays directly";
  el("flow-payment").textContent = money(plan.payment_cents || plan.bill_cents);
  el("flow-due").textContent = "Day " + due;
  el("flow-paid").textContent = "Day " + paid;
  el("mechanism-fuel").textContent = "Start with " + money(original) + " for " + plan.gemini.gallons
    + " gallons. " + (scenario === "fuel budget"
      ? "This plan uses the original bill. Higher oil prices from recorded Gemini contracts are also tested when sizing the reserve."
      : "Testing oil at $" + plan.gemini.stress_level + " a barrel adds " + money(plan.bill_cents - original) + " to the fuel budget. " + (plan.gemini.settled_reference
        ? "This estimate uses the reported prior settlement." : "That threshold comes from a recorded Gemini oil contract."));
  if (profile === "quick pay") {
    const invoice = plan.calendar.invoices.find(i => i.id === "backhaul");
    const early = plan.invoice_elections.find(i => i.invoice === invoice.id);
    el("mechanism-choice-title").textContent = "Collect the smaller invoice early";
    el("mechanism-choice").textContent = "Early pay on the " + money(invoice.face_cents) + " return load costs "
      + money(invoice.face_cents - early.receipt_cents) + ". Use its proceeds with the reserve and collect the full "
      + money(main.face_cents) + " on the main load.";
  } else {
    el("mechanism-choice-title").textContent = usesCredit(profile) ? "Borrow the balance" : "Use available cash";
    el("mechanism-choice").textContent = usesCredit(profile)
      ? "Borrow " + money(plan.other_funds_cents) + " from the bank and cover the rest from the reserve. Both freight invoices can pay on their usual dates."
      : "The driver has enough cash for fuel. Both freight invoices can pay on their usual dates and the financing fee is zero.";
  }
  el("preview-title").textContent = el("mechanism-choice-title").textContent;
  el("driver-story").textContent = profile === "quick pay"
    ? "Use " + money(plan.other_funds_cents) + " from the return load and " + money(plan.payment_cents)
      + " from your fuel reserve. Collect the full " + money(main.face_cents) + " freight invoice on day " + paid + "."
    : el("mechanism-choice").textContent;
  drawFunding(plan, profile === "quick pay" ? "Return-load invoice" : usesCredit(profile) ? "Bank credit" : "Your cash");
  el("mechanism-pay-title").textContent = plan.payment_cents ? "Send " + money(plan.payment_cents) + " to the fuel company" : "Record the direct payment";
  el("mechanism-pay").textContent = plan.payment_cents ? "The reserve holds digital dollars in Kamino, a lending vault on Solana. On approval, the payment program checks the fuel company and amount. The driver pays " + money(plan.other_funds_cents) + " separately."
    : "The driver pays the fuel company directly and records the payment. This cash plan needs no blockchain transaction.";
  el("quote-status").textContent = locked ? "Your bill already has a payment record or pending approval. These comparisons are previews." : "";
}

async function load() {
  el("quote-retry").hidden = true;
  el("quote-status").textContent = "Loading plan prices…";
  try {
    state = await request("/api/state");
    oilOptions(el("quote-scenario"), state.plans);
    displayQuote();
    el("quote-inputs").disabled = false;
    el("quote-result").hidden = false;
  } catch {
    el("quote-status").textContent = "Plan prices are unavailable. Try loading them again.";
    el("quote-retry").hidden = false;
  }
}
el("quote-inputs").onchange = displayQuote;
document.querySelector(".cash-choice").onchange = displayQuote;
el("quote-retry").onclick = load;
load();

request("/api/trial").then(report => {
  const edge = report.composition_savings;
  el("trial-proof-title").textContent = money(edge.total_cents) + " in lower fees";
  el("trial-proof-copy").textContent = "With the same reserve available to both strategies, FreightOS cost less on "
    + edge.positive_cases + " of " + report.cases + " simulated loads. The other " + edge.ties + " tied.";
}).catch(() => {});

el("replay-sample").onclick = () => verifySample("SYN-004", el("replay-sample"), el("sample-result"));

const flowTabs = [...document.querySelectorAll(".flow-nav [role=tab]")];
function showFlow(selected) {
  for (const tab of flowTabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    el(tab.getAttribute("aria-controls")).hidden = !active;
  }
}
for (const [index, tab] of flowTabs.entries()) {
  tab.onclick = () => showFlow(tab);
  tab.onkeydown = event => {
    const next = { ArrowRight: (index + 1) % flowTabs.length, ArrowLeft: (index + flowTabs.length - 1) % flowTabs.length,
      Home: 0, End: flowTabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    flowTabs[next].focus();
    showFlow(flowTabs[next]);
  };
}
