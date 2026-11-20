import { el, facts, money, request } from "./common.js";

const form = el("trip-cover-form");
const result = el("trip-result");
let current;
let revision = 0;

function generalBudget(d, later) {
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
  if (d.regional) rows.splice(1, 0, regionalCost(d.regional));
  facts(el("trip-options"), rows);
  const link = el("trip-reserve");
  link.hidden = !d.cash_only_cents;
  link.href = "/next-stop#fuel-buffer=" + d.cash_only_cents;
  link.textContent = "Check the " + money(d.cash_only_cents) + " cash buffer on Solana →";
  return plan;
}

function reserveBudget(d, later) {
  const reserve = d.reserve;
  const plan = later ? reserve.plan : null;
  const split = later ? reserve.split : null;
  const roadShort = Math.max(0, reserve.road_cents - reserve.available_cents);
  const fullBuffer = money(d.cash_only_cents);
  el("trip-bound").textContent = split ? "Extra money needed outside the reserve" : plan ? "Cost of oil protection on Gemini" : "Additional cash for the cash-only plan";
  el("trip-cash").textContent = money(split ? split.outside_total_cents : plan ? plan.upfront_cents : reserve.before_shortfall_cents);
  let why;
  if (roadShort) why = "The reserve is " + money(roadShort) + " below your road-cash target before buying fuel.";
  else if (plan?.legs.length) why = "Pay this from separate Gemini funds. With payouts spendable by the fill, the reserve can cover the rest through $" + d.ceiling + " oil.";
  else if (plan) why = "The reserve can cover extra fuel and leave your road cash available. Buying protection would add a fee.";
  else if (split) why = "Keep " + money(reserve.road_cents) + " for the road. You would need "
    + money(split.upfront_cents) + " in Gemini and another " + money(split.outside_fuel_cents)
    + " available for fuel. This assumes the payout is ready at the stop.";
  else if (later) why = "The available oil contracts cannot cover the modeled bill within this reserve limit. This is the cash-only backup.";
  else why = "Until payouts are spendable, budget the full " + fullBuffer + " for extra fuel. Keep " + money(reserve.road_cents) + " for the road.";
  el("trip-why").textContent = why;
  const rows = [["Available from the demo reserve", money(reserve.available_cents)], ["Requested for tolls and repairs", money(reserve.road_cents)],
    ["Reserve available for extra fuel", money(reserve.fuel_cash_cents)]];
  if (plan?.legs.length) rows.push(["Maximum fuel still due after payouts", money(plan.residual_cents)],
    ["Protection plus remaining fuel", money(plan.cash_cents)]);
  else if (split) rows.push(["Oil contracts including fees", money(split.upfront_cents)],
    ["Fuel to cover separately after payouts", money(split.outside_fuel_cents)],
    ["Extra money with cash alone", money(reserve.before_shortfall_cents)]);
  else rows.push(["Extra fuel in the cash-only plan", fullBuffer]);
  if (d.regional) rows.push(regionalCost(d.regional));
  facts(el("trip-options"), rows);
  const fuel = split ? split.reserve_cents : plan ? plan.residual_cents : d.cash_only_cents;
  const link = el("trip-reserve");
  link.hidden = !fuel;
  link.href = "/next-stop#fuel-buffer=" + fuel + "&road=" + reserve.road_cents;
  link.textContent = "Check the " + money(fuel) + " fuel payment and road cash →";
  el("reserve-observation").textContent = (d.source === "live" ? "Public Devnet check" : "Recorded Devnet check")
    + " · slot " + reserve.slot.toLocaleString("en-US") + ". The reserve funds the fuel payment. Buying contracts would require a separate funded Gemini account.";
  return split ? d.combined : plan;
}

function regionalCost(region) {
  return [region.name + " pump allowance included in estimate", money(region.allowance_cents)];
}

function render() {
  const d = current;
  const later = document.querySelector('input[name="timing"]:checked').value === "after";
  el("reserve-observation").hidden = !d.reserve;
  const plan = d.reserve ? reserveBudget(d, later) : generalBudget(d, later);
  const region = d.regional;
  el("regional-evidence").hidden = !region;
  if (region) {
    el("regional-test").textContent = "We added " + region.cents_per_gallon + "¢ per gallon for the " + region.name
      + ". That allowance came from the 90th percentile of weekly diesel changes left unexplained by oil across "
      + region.training_weeks + " earlier weeks.";
    el("regional-limit").textContent = "Using the oil move that actually happened, the regional pump price finished above the oil estimate in "
      + region.oil_only_misses + " of " + region.test_weeks + " later weeks. With the allowance, it exceeded the estimate in "
      + region.allowance_misses + ". The largest remaining gap was about " + region.worst_miss_cents + "¢ per gallon. "
      + "The check uses weekly EIA diesel and WTI spot prices through " + region.test_end + ". Your station price and the time until you fill up can differ.";
  }
  const legs = plan?.legs || [];
  el("trip-method").textContent = "The model starts from your diesel price and Gemini’s $" + Number(d.anchor).toFixed(2)
    + " oil close. It uses the sample trip’s diesel sensitivity and tests closes through $" + d.ceiling
    + " per barrel. " + d.offers + " of " + d.contracts_checked + " thresholds had matching offers and displayed quantity. They expire "
    + new Date(d.expiry).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" }) + " UTC.";
  facts(el("trip-legs"), legs.map(leg => ["Oil at $" + leg.strike + " or higher", (leg.lots / 100).toFixed(2) + " units at " + money(leg.price)]));
  const fees = legs.reduce((sum, leg) => sum + leg.fee_cents, 0);
  el("trip-fees").textContent = legs.length ? "The mix costs " + money(plan.upfront_cents) + " including " + money(fees)
    + " in estimated fees. Each winning unit pays $1. Assumes fills at displayed prices and zero transfer cost. Local diesel prices may differ. No orders placed."
    : "The model uses available offers at the best displayed price for each threshold. Local diesel prices may differ. No orders placed.";
  if (d.reserve && legs.length) {
    el("trip-fees").textContent += " With this mix already bought, delayed payouts would still leave a "
      + money(d.reserve.before_shortfall_cents) + " fuel and road-cash shortfall.";
  }
  el("trip-pending").hidden = true;
  result.hidden = false;
}

async function compare(source) {
  const attempt = ++revision;
  const status = el("trip-status");
  el("trip-check").disabled = el("trip-recorded").disabled = true;
  const includeReserve = el("use-trip-reserve").checked;
  if (source === "live") status.textContent = includeReserve ? "Checking Gemini prices and your Solana reserve…" : "Checking Gemini prices…";
  if (source === "recorded") status.textContent = "Opening the recorded comparison…";
  try {
    const body = source ? { source, gallons: el("trip-gallons").valueAsNumber, pump_mills: Math.round(el("trip-pump").valueAsNumber * 1000),
      budget_cents: Math.round(el("trip-budget").valueAsNumber * 100) } : undefined;
    if (body && el("trip-region").value) body.region = el("trip-region").value;
    if (body && includeReserve) body.road_cents = Math.round(el("trip-road").valueAsNumber * 100);
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
  el("trip-road").disabled = el("trip-road-fields").hidden = !el("use-trip-reserve").checked;
  current = null;
  result.hidden = el("trip-recorded").hidden = true;
  el("trip-check").disabled = el("trip-pending").hidden = false;
  el("trip-status").textContent = "Compare these amounts to update your budget.";
};
form.onsubmit = event => { event.preventDefault(); compare("live"); };
el("trip-recorded").onclick = () => { if (form.reportValidity()) compare("recorded"); };
document.querySelector(".trip-timing").onchange = () => { if (current) render(); };
function openExample() {
  form.reset();
  const example = new URLSearchParams(location.hash.slice(1));
  const region = example.get("region");
  const includeReserve = example.has("road-cash");
  if ([...el("trip-region").options].some(option => option.value === region)) el("trip-region").value = region;
  el("use-trip-reserve").checked = includeReserve;
  el("trip-road").disabled = el("trip-road-fields").hidden = !includeReserve;
  document.querySelector('input[name="timing"][value="before"]').checked = true;
  current = null;
  result.hidden = el("trip-recorded").hidden = true;
  el("trip-pending").hidden = true;
  el("trip-status").textContent = "Opening the sample trip…";
  compare(el("trip-region").value || includeReserve ? "recorded" : undefined);
}
window.addEventListener("hashchange", openExample);
openExample();
