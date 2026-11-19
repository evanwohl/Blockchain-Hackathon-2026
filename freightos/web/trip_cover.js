import { el, facts, money, request } from "./common.js";

const form = el("trip-cover-form");
const result = el("trip-result");
let current;
let revision = 0;

function render() {
  const d = current;
  const later = document.querySelector('input[name="timing"]:checked').value === "after";
  const plan = d.combined;
  const hasCover = plan.legs.length > 0;
  const beforeCost = plan.upfront_cents + d.cash_only_cents;
  el("trip-bound").textContent = "Cash to set aside through $" + d.ceiling + " oil";
  el("trip-cash").textContent = money(later ? plan.cash_cents : d.cash_only_cents);
  el("trip-why").textContent = !d.cash_only_cents ? "Your budget covers the modeled trip through $" + d.ceiling + " oil."
    : later && hasCover ? "This includes " + money(plan.upfront_cents) + " to buy the contracts. If payouts arrive late, the same plan needs " + money(beforeCost) + " in all."
    : "Keep cash available for the fill. " + (hasCover ? "Buying oil contracts would also use " + money(plan.upfront_cents) + " before the stop."
      : "The checked prices give no lower cash requirement.");
  const rows = [["Diesel at the entered price", money(d.pump_bill_cents)], ["Extra cash without a hedge", money(d.cash_only_cents)]];
  if (later && d.offers) rows.push(["With the best single oil contract", money(d.best_single_cents)], ["With the lowest-cost mix", money(plan.cash_cents)]);
  if (!later && hasCover) rows.push(["With contracts bought before the stop", money(beforeCost)]);
  facts(el("trip-options"), rows);
  const link = el("trip-reserve");
  link.hidden = !d.cash_only_cents;
  link.href = "/next-stop#fuel-buffer=" + d.cash_only_cents;
  link.textContent = "Check the " + money(d.cash_only_cents) + " cash buffer on Solana →";
  el("trip-method").textContent = "The model starts from your diesel price and Gemini’s $" + Number(d.anchor).toFixed(2)
    + " oil close. It uses the sample trip’s diesel sensitivity and tests closes through $" + d.ceiling
    + " per barrel. " + d.offers + " of " + d.contracts_checked + " thresholds had matching offers and displayed quantity. They expire "
    + new Date(d.expiry).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" }) + " UTC.";
  facts(el("trip-legs"), plan.legs.map(leg => ["Oil at $" + leg.strike + " or higher", (leg.lots / 100).toFixed(2) + " units at " + money(leg.price)]));
  const fees = plan.legs.reduce((sum, leg) => sum + leg.fee_cents, 0);
  el("trip-fees").textContent = hasCover ? "The mix costs " + money(plan.upfront_cents) + " including " + money(fees)
    + " in estimated fees. Each winning unit pays $1. Assumes fills at displayed prices and zero transfer cost. Local diesel prices may differ. No orders placed."
    : "The model uses available offers at the best displayed price for each threshold. Local diesel prices may differ. No orders placed.";
  el("trip-pending").hidden = true;
  result.hidden = false;
}

async function compare(source) {
  const attempt = ++revision;
  const status = el("trip-status");
  el("trip-check").disabled = el("trip-recorded").disabled = true;
  if (source) status.textContent = source === "live" ? "Checking the oil order books…" : "Checking the recorded prices…";
  try {
    const body = source ? { source, gallons: el("trip-gallons").valueAsNumber, pump_mills: Math.round(el("trip-pump").valueAsNumber * 1000),
      budget_cents: Math.round(el("trip-budget").valueAsNumber * 100) } : undefined;
    const data = await request("/api/trip-cover", body, AbortSignal.timeout(45000));
    if (attempt !== revision) return;
    current = data;
    render();
    const stamp = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
    status.textContent = (data.source === "live" ? "Checked with Gemini" : "Recorded Gemini prices") + " · " + stamp + " UTC";
    el("trip-recorded").hidden = true;
  } catch {
    if (attempt !== revision) return;
    status.textContent = current ? "Refresh unavailable. The previous comparison is still shown." : "Comparison unavailable. Try again or use the recorded prices.";
    el("trip-recorded").hidden = false;
  } finally {
    if (attempt === revision) el("trip-check").disabled = el("trip-recorded").disabled = false;
  }
}

form.oninput = () => {
  revision++;
  current = null;
  result.hidden = el("trip-recorded").hidden = true;
  el("trip-check").disabled = el("trip-pending").hidden = false;
  el("trip-status").textContent = "Compare these amounts to update your budget.";
};
form.onsubmit = event => { event.preventDefault(); compare("live"); };
el("trip-recorded").onclick = () => { if (form.reportValidity()) compare("recorded"); };
document.querySelector(".trip-timing").onchange = () => { if (current) render(); };
compare();
