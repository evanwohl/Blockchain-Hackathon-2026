import { el, facts, money, request } from "./common.js";

import { setFuelGap } from "./trip_credit.js";

const form = el("trip-cover-form");
const result = el("trip-result");
let current;
let useRiskMix = false;
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
  else if (d.trip.spend_limit_cents === 0) why = "Keep " + money(reserve.road_cents) + " for the road. Your zero spending limit uses cash for fuel.";
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
  const cashOnlyDraw = d.trip.spend_limit_cents === undefined ? d.cash_only_cents : Math.min(d.cash_only_cents, reserve.fuel_cash_cents);
  const fuel = split ? split.reserve_cents : plan ? plan.residual_cents : cashOnlyDraw;
  const link = el("trip-reserve");
  link.hidden = !fuel;
  link.href = "/next-stop#fuel-buffer=" + fuel + "&road=" + reserve.road_cents;
  link.textContent = "Check the " + money(fuel) + " fuel payment and road cash →";
  return split ? d.combined : plan;
}

function targetBudget(d, later) {
  const target = d.pump_target;
  const payment = later ? target.after : target.before;
  const cost = later ? target.plan.upfront_cents : 0;
  const reserve = d.reserve;
  const roadShort = reserve ? Math.max(0, reserve.road_cents - reserve.available_cents) : 0;
  const missing = payment.short_cents + roadShort;
  el("trip-bound").textContent = missing ? (roadShort ? "Still needed for fuel and road cash" : "Short at the pump") : "Least to spend on oil protection";
  el("trip-cash").textContent = money(missing || cost);
  let why = cost ? "Spend " + money(cost) + " on Gemini. " : "Use cash for this fill. ";
  why += "Allow " + money(payment.outside_cents) + " beyond your fuel budget at the pump";
  why += payment.reserve_cents ? ", plus " + money(payment.reserve_cents) + " from your reserve." : ".";
  if (roadShort) why += " Your reserve also needs " + money(roadShort) + " to reach your road-cash target.";
  else if (!later && missing) why += " Oil payouts arrive after this stop.";
  else if (later && payment.short_cents && target.minimum_spend_cents !== null) {
    why += " Meeting your pump limit would require " + money(target.minimum_spend_cents) + " in Gemini.";
  } else if (later && payment.short_cents) why += " This is the closest the available contracts can get to your pump limit.";
  el("trip-why").textContent = why;
  const rows = [["Extra pump cash you have", money(d.trip.pump_cash_cents)], ["Extra pump cash needed", money(payment.outside_cents)]];
  if (reserve) rows.push(["Reserve payment for fuel", money(payment.reserve_cents)], ["Money kept for the road", money(Math.min(reserve.road_cents, reserve.available_cents))]);
  if (later && target.minimum_spend_cents > 0) {
    rows.push(["Minimum for this pump limit using one oil threshold",
      target.single_spend_cents === null ? "Out of reach" : money(target.single_spend_cents)]);
  }
  if (d.trip.spend_limit_cents !== undefined) rows.push(["Gemini spending limit", money(d.trip.spend_limit_cents)]);
  if (d.regional) rows.push(regionalCost(d.regional));
  facts(el("trip-options"), rows);
  const link = el("trip-reserve");
  link.hidden = !payment.reserve_cents;
  if (reserve) {
    link.href = "/next-stop#fuel-buffer=" + payment.reserve_cents + "&road=" + reserve.road_cents;
    link.textContent = "Preview the " + money(payment.reserve_cents) + " reserve payment →";
  }
  return later ? target.plan : null;
}

function showProtectionChoice(target, later) {
  const change = later && target?.protection_change;
  el("protection-choice").hidden = !change;
  if (!change) return;
  el("protection-extra").textContent = money(change.extra_cents);
  el("protection-covered").textContent = money(target.after.short_cents);
  el("protection-terms").textContent = "Raising your protection budget to " + money(change.total_cents)
    + " covers the modeled fuel gap if payouts arrive before the stop. If payouts arrive late, you still need "
    + money(target.before.short_cents) + " at the pump.";
  const outcomes = change.outcomes;
  facts(el("protection-outcomes"), outcomes.map((row, index) => {
    const next = outcomes[index + 1]?.from_strike;
    const label = index === 0 ? "Oil below $" + next : next ? "$" + row.from_strike + " to below $" + next : "$" + row.from_strike + " or higher";
    return [label, money(row.extra_payout_cents)];
  }));
  const largest = Math.max(1, ...outcomes.map(row => Math.abs(row.extra_payout_cents)));
  for (const [index, cell] of [...el("protection-outcomes").querySelectorAll("dd")].entries()) {
    const payout = outcomes[index].extra_payout_cents;
    cell.style.setProperty("--payout", (100 * Math.abs(payout) / largest) + "%");
    cell.dataset.loss = String(payout < 0);
  }
  const range = change.market_value;
  el("protection-value").textContent = "These are changes in settlement payout from switching plans before buying. The extra premium is paid in every outcome."
    + (range ? " Prices consistent with Gemini’s displayed quotes put the added payouts at " + money(range.low_cents) + " to "
      + money(range.high_cents) + " before fees. Realized payouts depend on where oil settles." : "");
  const draw = el("protection-draw");
  draw.hidden = !range;
  if (range) draw.textContent = "With your current budget, the largest modeled draw is " + money(target.after.short_cents)
    + ". Weighting possible draws using prices consistent with the same quotes gives " + money(change.draw_low_cents)
    + " to " + money(change.draw_high_cents) + ". This assumes timely payouts, the trip’s diesel estimate and oil capped at the tested ceiling. Actual borrowing can differ.";
  el("protection-try").textContent = "Try the " + money(change.total_cents) + " budget";
}

function regionalCost(region) {
  return [region.name + " pump allowance included in estimate", money(region.allowance_cents)];
}

function showSpendingLimit(d, later) {
  const limit = d.trip.spend_limit_cents;
  el("spending-result").hidden = limit === undefined;
  if (limit === undefined) return;
  el("limit-title").textContent = "Oil protection limit · " + money(limit);
  const change = later && d.limit_comparison;
  el("limit-benchmark").hidden = el("limit-difference").hidden = !change;
  if (change) {
    el("limit-benchmark").textContent = "Compared with spending " + money(change.unrestricted_upfront_cents) + " on protection.";
    const difference = change.outside_change_cents;
    facts(el("limit-difference"), [["Less spent upfront", money(change.kept_upfront_cents)],
      [difference < 0 ? "Less needed overall" : "Extra needed overall", money(Math.abs(difference))]]);
  }
  const minimum = d.reserve?.minimum_spend_cents;
  el("limit-threshold").hidden = !later || minimum == null || limit >= minimum;
  if (!el("limit-threshold").hidden) {
    el("limit-threshold").textContent = "Covering the rest entirely from the reserve after payouts would require at least "
      + money(minimum) + " for protection.";
  }
}

function render() {
  let d = current;
  const later = document.querySelector('input[name="timing"]:checked').value === "after";
  const target = d.pump_target;
  const risk = later && target?.funding_risk;
  const alternative = risk?.alternative;
  el("funding-risk").hidden = !risk;
  if (risk) {
    el("risk-weight").textContent = "Oil ranges where extra cash may be needed carry up to " + risk.after_pct
      + "% of the weight implied by Gemini prices with the smaller-gap plan."
      + (alternative ? " The other mix reduces that bound to " + alternative.weight_pct + "%." : "")
      + " A range counts if any tested price leaves a gap. These weights describe market pricing.";
    el("risk-switch").hidden = !alternative;
    el("risk-tradeoff").textContent = alternative ? "Within the same " + money(target.plan.upfront_cents)
      + " protection budget, the other mix covers different oil prices. Your largest modeled fuel gap rises from "
      + money(target.after.short_cents) + " to " + money(alternative.short_cents) + "."
      : "The tested mixes gave no verified improvement within one extra gallon’s cost in the worst case.";
    el("risk-switch").textContent = useRiskMix ? "Return to the smaller peak gap" : "Preview the other mix";
    if (alternative && useRiskMix) d = {...d, pump_target: {...target, ...alternative}};
  }
  el("reserve-observation").hidden = !d.reserve;
  if (d.reserve) {
    el("reserve-observation").textContent = (d.source === "live" ? "Public Devnet check" : "Recorded Devnet check")
      + " · slot " + d.reserve.slot.toLocaleString("en-US") + ". The reserve amount comes from simulating a withdrawal of your Kamino vault shares. Oil protection uses separate Gemini funds.";
  }
  let plan;
  if (d.pump_target) {
    plan = targetBudget(d, later);
    el("spending-result").hidden = true;
  } else {
    plan = d.reserve ? reserveBudget(d, later) : generalBudget(d, later);
    showSpendingLimit(d, later);
  }
  showProtectionChoice(d.pump_target, later);
  setFuelGap(d.pump_target ? (later ? d.pump_target.after : d.pump_target.before).short_cents : 0,
    later ? d.pump_target?.protection_change : null);
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
    const four = region.four_fills;
    el("regional-tail").hidden = !four;
    if (four) el("regional-tail").textContent = "For " + d.trip.gallons + " gallons each week, the worst tenth of "
      + four.windows + " overlapping four-fill runs averaged " + money(four.tail_cents)
      + " in extra fuel cash above the regional allowance. Each fill starts from the previous week’s price. Cheaper weeks contribute zero.";
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

async function compare(source, submitted = false) {
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
    if (body && el("trip-limit").value !== "") body.spend_limit_cents = Math.round(el("trip-limit").valueAsNumber * 100);
    if (body && el("pump-cash").value !== "") body.pump_cash_cents = Math.round(el("pump-cash").valueAsNumber * 100);
    if (body && includeReserve) body.road_cents = Math.round(el("trip-road").valueAsNumber * 100);
    const data = await request("/api/trip-cover", body, AbortSignal.timeout(45000));
    if (attempt !== revision) return;
    useRiskMix = false;
    current = data;
    render();
    const stamp = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
    status.textContent = (data.source === "live" ? "Checked with Gemini" : "Recorded Gemini prices") + " · " + stamp + " UTC";
    el("trip-recorded").hidden = true;
    if (submitted && matchMedia("(max-width: 720px)").matches && (form.contains(document.activeElement) || document.activeElement === document.body || document.activeElement === status)) {
      el("trip-heading").focus();
    }
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
  setFuelGap(0);
  current = null;
  result.hidden = el("trip-recorded").hidden = true;
  el("trip-check").disabled = el("trip-pending").hidden = false;
  el("trip-status").textContent = "Compare these amounts to update your budget.";
};
el("risk-switch").onclick = () => {
  useRiskMix = !useRiskMix;
  render();
};
el("protection-try").onclick = () => {
  const change = current?.pump_target?.protection_change;
  if (!change) return;
  const source = current.source;
  el("trip-limit").value = (change.total_cents / 100).toFixed(2);
  form.dispatchEvent(new Event("input"));
  el("trip-status").focus();
  compare(source, true);
};
form.onsubmit = event => { event.preventDefault(); compare("live", true); };
el("trip-recorded").onclick = () => { if (form.reportValidity()) compare("recorded", true); };
document.querySelector(".trip-timing").onchange = () => { if (current) { useRiskMix = false; render(); } };
function openExample() {
  form.reset();
  const example = new URLSearchParams(location.hash.slice(1));
  const region = example.get("region");
  const includeReserve = example.has("road-cash");
  if ([...el("trip-region").options].some(option => option.value === region)) el("trip-region").value = region;
  for (const [key, field] of [["limit", "trip-limit"], ["pump", "pump-cash"]]) {
    const amount = example.get(key);
    if (amount !== null && /^\d{1,7}(\.\d{1,2})?$/.test(amount) && Number(amount) <= 1000000) {
      el(field).value = Number(amount).toFixed(2);
    }
  }
  const hasLimit = el("trip-limit").value !== "" || el("pump-cash").value !== "";
  el("protection-limit").open = hasLimit;
  el("use-trip-reserve").checked = includeReserve;
  el("trip-road").disabled = el("trip-road-fields").hidden = !includeReserve;
  const timing = example.get("timing") === "after" ? "after" : "before";
  document.querySelector('input[name="timing"][value="' + timing + '"]').checked = true;
  setFuelGap(0);
  current = null;
  result.hidden = el("trip-recorded").hidden = true;
  el("trip-pending").hidden = true;
  el("trip-status").textContent = "Opening the sample trip…";
  compare(el("trip-region").value || includeReserve || hasLimit ? "recorded" : undefined);
}
window.addEventListener("hashchange", openExample);
openExample();
