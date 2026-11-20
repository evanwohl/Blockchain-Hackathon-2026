import { el, facts, money, request, tokenUnits } from "./common.js";

const form = el("stop-form");
const button = el("stop-check");
const status = el("stop-status");
const result = el("stop-result");
let revision = 0;

const pumpPrice = mills => "$" + (mills / 1000).toFixed(3);

function singleStop(data, model) {
  const fits = data.shortfall_raw === 0;
  el("stop-verdict").textContent = fits ? (data.road_cents ? "The reserve covers both." : "The reserve covers this fuel payment.") : money(Math.ceil(data.shortfall_raw / data.raw_units_per_cent)) + " more needed.";
  facts(el("stop-breakdown"), [
    ["Available from the reserve", model(data.available_raw)],
    ...(fits ? [["To the fuel company", model(data.vendor_raw)], ["Back to the driver’s wallet", model(data.wallet_increase_raw)]]
      : [["Fuel and road cash requested", money(data.fuel_cents + data.road_cents)]]),
    ...(data.retained_shares_raw == null ? [] : [["Still available from the reserve", model(data.retained_raw)]])
  ]);
  result.dataset.covered = String(fits);
  el("stop-network").textContent = "At slot " + data.slot.toLocaleString("en-US") + ", the simulated withdrawal released "
    + tokenUnits(fits ? data.vendor_raw + data.wallet_increase_raw : data.available_raw) + " test USDC. " + (fits
      ? "A single transaction preview credited " + tokenUnits(data.vendor_raw) + " to the vendor and added "
        + tokenUnits(data.wallet_increase_raw) + " to the driver’s wallet. The quoted fee was " + data.network_fee_lamports / 1e9 + " test SOL."
      : "That falls short of the two requested amounts. Reduce either amount to check a smaller stop.");
  if (data.retained_shares_raw > 0) el("stop-network").textContent += " A second simulation redeemed the "
    + tokenUnits(data.retained_shares_raw) + " remaining shares to verify their value.";
  if (!data.eligible_shares_raw) el("stop-network").textContent = "The driver held no eligible shares at slot " + data.slot.toLocaleString("en-US") + ".";
}

function twoStops(data, model) {
  const future = data.holdback;
  const verified = data.retained_raw != null;
  const now = model(data.vendor_raw);
  el("stop-verdict").textContent = verified ? now + " for this stop." : "The reserve can’t cover this stop.";
  result.dataset.covered = String(verified && data.vendor_raw === data.fuel_cents * data.raw_units_per_cent);
  const rows = [["Available from both paid bills", model(data.available_raw)]];
  if (verified) {
    rows.push(["To the fuel company", now], ["Back in your wallet", model(data.wallet_increase_raw)],
      ["Left in your fuel reserve", model(data.retained_raw)]);
    el("stop-adjustment").textContent = (data.vendor_raw < data.fuel_cents * data.raw_units_per_cent
      ? "Reduce the planned fill by " + money(data.fuel_cents - data.budget_limit_cents) + " to cover both stops. "
      : "The planned fill fits. ") + "The " + future.gallons + "-gallon estimate for later is " + money(future.cost_cents) + ".";
    el("stop-network").textContent = "Devnet simulated the fuel payment and wallet return. A second simulation repeated those instructions and redeemed the remaining "
      + tokenUnits(data.retained_shares_raw) + " shares, releasing another " + tokenUnits(data.retained_raw)
      + " test USDC at slot " + data.slot.toLocaleString("en-US") + ".";
  } else {
    rows.push(["Needed for later fuel", money(future.cost_cents)], ["Requested for your wallet", money(data.road_cents)]);
    el("stop-adjustment").textContent = "The full plan needs " + money(Math.ceil(data.shortfall_raw / data.raw_units_per_cent))
      + " more. Try fewer gallons or less cash back.";
    el("stop-network").textContent = "The available balance was checked on Devnet at slot " + data.slot.toLocaleString("en-US") + ".";
  }
  facts(el("stop-breakdown"), rows);
  const pump = future.pump_mills;
  const basis = pump == null
    ? "The trip estimate uses " + money(future.trip_cents) + " for " + future.trip_gallons + " gallons."
    : "Your entered price is " + pumpPrice(pump) + " per gallon.";
  const oil = "$" + Number(future.oil_close).toFixed(2);
  const threshold = "$" + Number(future.threshold).toLocaleString("en-US");
  el("stop-rise-label").textContent = "Budget for oil to reach " + threshold + " per barrel";
  el("gallon-method").textContent = basis + " At that rate, " + future.gallons + " gallons come to " + money(future.base_cents) + " rounded up."
    + (future.oil_rise ? " Using the trip’s oil-to-diesel estimate, a move from Gemini’s " + oil + " close to " + threshold
      + " adds " + money(future.extra_cents) + " to the budget. The actual pump price can move differently."
      : pump == null ? " Gemini’s recorded oil close was " + oil + " per barrel." : " The budget uses your entered price.");
  const events = "https://api.gemini.com/v1/prediction-markets/events/";
  el("gallon-event").hidden = pump != null && !future.oil_rise;
  el("gallon-event").href = events + encodeURIComponent(future.event);
  el("scenario-event").hidden = !future.oil_rise;
  el("scenario-event").href = events + encodeURIComponent(future.scenario_event);
  if (verified) {
    el("pump-limit").hidden = false;
    el("pump-limit").textContent = "At the later stop, this reserve covers " + future.gallons + " gallons up to "
      + pumpPrice(data.covered_pump_mills) + " per gallon at the checked balance.";
  }
}

function render(data) {
  const model = raw => money(Math.floor(raw / data.raw_units_per_cent));
  const stamp = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
  status.textContent = (data.source === "saved" ? "Saved Devnet simulation" : "Checked on Devnet") + " · " + stamp + " UTC";
  result.dataset.lastCheck = stamp + " UTC";
  facts(el("share-history"), [
    ["From the first payment", tokenUnits(data.first_shares_raw) + " shares"],
    ["From the customer and reserve payment", tokenUnits(data.offer_shares_raw) + " shares"],
    ["Wallet cash after the second payment", tokenUnits(data.wallet_after_settlement_raw) + " test USDC"],
    ["Eligible at this check", tokenUnits(data.eligible_shares_raw) + " shares"]
  ]);
  el("share-check").textContent = "Reserve check at slot " + data.slot.toLocaleString("en-US") + " · " + stamp + " UTC";
  const future = data.holdback;
  el("gallon-assumption").hidden = el("stop-adjustment").hidden = !future;
  const download = el("stop-transcript");
  download.href = future ? "/evidence/two-stops" : "/evidence/next-stop";
  download.download = future ? "FreightOS-saved-two-stops.json" : "FreightOS-saved-next-stop.json";
  download.textContent = future ? "Download the original three-gallon simulation" : "Download the original $20 simulation";
  el("pump-limit").hidden = true;
  if (future) twoStops(data, model);
  else singleStop(data, model);
  result.hidden = false;
}

async function check(live) {
  const attempt = ++revision;
  button.disabled = true;
  if (live) status.textContent = "Checking the paid receipts, reserve and fuel payment…";
  try {
    const body = live ? { fuel_cents: Math.round(el("stop-fuel").valueAsNumber * 100), road_cents: Math.round(el("stop-road").valueAsNumber * 100) } : undefined;
    const later = el("keep-fuel").checked;
    if (live && !later) body.keep_shares = el("keep-shares").checked;
    if (live && later) {
      body.gallons = el("stop-gallons").valueAsNumber;
      body.oil_rise = el("stop-rise").checked;
      if (el("stop-pump").value !== "") body.pump_mills = Math.round(el("stop-pump").valueAsNumber * 1000);
    }
    const data = await request(later ? "/api/two-stops" : "/api/next-stop", body, AbortSignal.timeout(45000));
    if (attempt === revision) render(data);
  } catch {
    if (attempt === revision) {
      status.textContent = result.hidden ? "The preview is unavailable. Try checking again."
        : "The new check is unavailable. Showing the previous check from " + result.dataset.lastCheck + ".";
    }
  } finally {
    if (attempt === revision) button.disabled = false;
  }
}
function updateMode() {
  const later = el("keep-fuel").checked;
  el("stop-fuel-label").textContent = later ? "Planned fill now" : buffer?.[3] ? "Additional fuel cash needed" : "Fuel bill";
  el("stop-later").hidden = !later;
  el("keep-shares").disabled = el("keep-shares-label").hidden = later;
  for (const id of ["stop-gallons", "stop-rise", "stop-pump"]) el(id).disabled = !later;
}
form.oninput = () => {
  updateMode();
  revision++;
  result.hidden = true;
  button.disabled = false;
  status.textContent = "Check these amounts for an updated preview.";
};
form.onsubmit = event => { event.preventDefault(); check(true); };
if (location.hash === "#two-stops") {
  el("keep-fuel").checked = true;
  el("stop-fuel").value = "15.00";
  el("stop-road").value = "5.00";
}
const buffer = /^#fuel-buffer=([1-9][0-9]{0,8})(?:&road=([0-9]{1,9}))?(&keep-shares)?$/.exec(location.hash);
if (buffer) {
  el("keep-shares").checked = Boolean(buffer[3]);
  el("stop-fuel").value = (Number(buffer[1]) / 100).toFixed(2);
  el("stop-road").value = (Number(buffer[2] || 0) / 100).toFixed(2);
}
updateMode();
check(Boolean(buffer));
