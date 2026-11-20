import { el, facts, money, saveFile } from "./common.js";

const title = net => net ? money(net) + " early" : "Wait for the full invoice";
const method = plan => plan.kind === "loan" ? "secured loan" : plan.sold_lamports ? "exchange" : "wallet";

function offerSummary(rows) {
  const viable = rows.filter(p => !p.entry_gap && !p.payday_gap && !p.outside_stress);
  viable.sort((a,b) => a.regret_cents - b.regret_cents || b.least_cents - a.least_cents || a.entry_lamports - b.entry_lamports);
  if (viable.length) return method(viable[0]) + " · at least " + money(viable[0].least_cents) + " left in the tested cases";
  const sound = rows.filter(p => !p.payday_gap && !p.outside_stress).sort((a,b) => a.entry_cents - b.entry_cents);
  if (sound.length) return "Needs a " + money(sound[0].entry_cents) + " wallet budget";
  const affordable = rows.filter(p => !p.entry_gap && !p.outside_stress).sort((a,b) => a.payday_gap - b.payday_gap);
  return affordable.length ? "Payday target short by " + money(affordable[0].payday_gap) : "No checked route fits both limits";
}

export function showPolicy(d) {
  const w = d.winner;
  el("credit-heading").textContent = !w ? "Both limits cannot be met" : w.customer_cents ? "Ask for " + money(w.customer_cents) + " early" : "Keep the full invoice";
  el("policy-label").textContent = w ? "Fuel covered at the stop" : "Fuel still to arrange";
  el("policy-total").textContent = money(d.bill_cents);
  el("policy-draft").hidden = !w?.customer_cents;
  el("policy-strip").hidden = !w;
  if (w) {
    const sources = [w.customer_cents, w.cash_used_cents, w.reserve_cents, w.venue_cents];
    el("policy-strip").querySelectorAll("i").forEach((bar,i) => { bar.style.width = (100*sources[i]/d.bill_cents) + "%"; });
    el("policy-action").textContent = (w.customer_cents ? "Confirm the " + money(w.customer_fee_cents) + " fee and wait for the customer’s " + money(w.customer_cents) + " to clear. " : "No early customer payment is needed. ")
      + (w.kind === "loan" ? "Pledge SOL for the remaining loan." : w.sold_lamports ? "Use the exchange to finish the payment without borrowing." : "Use the spendable wallet balance.");
    facts(el("policy-sources"), [["Customer pays before the stop", money(w.customer_cents)], ["Existing wallet cash", money(w.cash_used_cents)],
      ["Fuel reserve", money(w.reserve_cents)], [w.kind === "loan" ? "Borrowed against SOL" : "From selling SOL", money(w.venue_cents)],
      ["Still due after the payments clear", money(0)]]);
    el("policy-payday").textContent = "The customer still owes " + money(w.invoice_cents) + ". After refilling the reserve and restoring spendable funds, the plan leaves "
      + money(w.least_cents) + " to " + money(w.most_cents) + " over the tested price range. " + money(w.reserve_left_cents) + " remains in the vault for the next fuel stop.";
    el("policy-draft").onclick = () => saveFile("FreightOS-early-payment-request.txt", "Invoice [invoice or load ID]\n\nPlease confirm that " + money(w.customer_cents)
      + " can clear before [fuel purchase date]. Including your fee of " + money(w.customer_fee_cents) + ", that leaves " + money(w.invoice_cents)
      + " of the $200 freight invoice due on [regular payment date].\n", "text/plain");
  } else {
    const paying = d.plans.filter(p => !p.entry_gap && !p.outside_stress).sort((a,b) => b.least_cents-a.least_cents);
    const smallest = Math.min(...d.plans.map(p => p.entry_cents));
    el("policy-label").textContent = paying.length ? "Best cash left from freight in the weakest case" : "Smallest checked opening budget";
    el("policy-total").textContent = money(paying.length ? paying[0].least_cents : smallest);
    el("policy-action").textContent = paying.length ? "Fuel can be covered today. The strongest payday result is still " + money(paying[0].payday_gap)
      + " short of the " + money(d.floor_cents) + " target in the weakest price case." : "No checked plan fits both the current wallet and the price range.";
    facts(el("policy-sources"), [["Fuel due before freight pays", money(d.bill_cents)], ["Cash needed from the invoice on payday", money(d.floor_cents)]]);
    el("policy-payday").textContent = "The comparison includes refilling the reserve, repaying debt and replacing wallet assets used for this stop.";
  }
  const wait = d.plans.find(p => !p.customer_cents && p.entry_lamports === d.wait_entry && !p.payday_gap && !p.outside_stress);
  el("policy-wait").textContent = wait ? "To leave the whole invoice on its regular terms, the smallest checked wallet budget is " + money(wait.entry_cents)
    + (wait.entry_gap ? ". That exceeds what is available today." : ". It fits your budget.") : "Waiting for the whole invoice misses the payday target or falls outside the loan stress in the checked routes.";
  const tryWait = el("policy-try-wait");
  tryWait.hidden = !wait || !wait.entry_gap || wait.entry_lamports > d.actual_lamports;
  if (!tryWait.hidden) {
    tryWait.textContent = "Compare with " + money(wait.entry_cents) + " available";
    tryWait.onclick = () => {
      el("policy-budget").value = (wait.entry_cents / 100).toFixed(2);
      el("policy-budget").dispatchEvent(new Event("change", {bubbles: true}));
      el("policy-budget").focus();
    };
  }
  if (w && d.minimum_early_cents < w.customer_cents) el("policy-wait").textContent += " A smaller advance of " + money(d.minimum_early_cents) + " also fits, with a different payday result.";
  facts(el("policy-offers"), [...new Set(d.plans.map(p => p.customer_cents))].map(net => [title(net), offerSummary(d.plans.filter(p => p.customer_cents === net))]));
  el("policy-rule").textContent = w ? "Among plans that meet both limits in every case, this choice leaves at most " + money(w.regret_cents)
    + " less than choosing with the payday price known in advance. The comparison checks every whole-cent price from " + money(Math.min(...d.cases.map(c => c.price_cents))) + " to " + money(Math.max(...d.cases.map(c => c.price_cents))) + ". A tie favors more cash in the weakest case, then less early customer money." : "Only customer offers made available in the form were considered.";
  const history = d.cases.filter(c => c.start);
  el("policy-evidence").textContent = history.length + " historical 30-day moves come from Gemini SOL contract settlements. Applied to the common " + money(d.reference_cents)
    + " reference, they test how much replacing SOL could cost. " + d.excluded.length + " windows crossed an index change and were excluded. The windows overlap and carry no probability weights.";
  facts(el("policy-moves"), d.cases.map(c => [c.label, money(c.price_cents)]));
  el("policy-assumptions").textContent = "Devnet slots " + d.first_slot.toLocaleString("en-US") + "–" + d.last_slot.toLocaleString("en-US") + ". Each plan keeps the Gemini oil-based fuel allowance. Sales include the SOL sold and network fee. Loans assume repayment and collateral release after 30 days at twice the observed rate, within the tested 40% SOL drop. "
    + (d.keep_profile ? "The driver can leave setup money in the loan profile." : "The plan replaces SOL still tied up in the loan profile.") + " Future replacement uses the tested prices. Intermediate liquidation paths, taxes and future yield are outside this comparison. Dollar amounts use the demo scale and exchange prices come from Solana’s test network.";
  el("policy-result").hidden = false;
}
