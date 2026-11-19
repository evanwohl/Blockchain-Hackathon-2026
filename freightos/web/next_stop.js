import { el, facts, money, request, tokenUnits } from "./common.js";

const form = el("stop-form");
const button = el("stop-check");
const status = el("stop-status");
const result = el("stop-result");
let revision = 0;

function render(data) {
  const model = raw => money(Math.floor(raw / data.raw_units_per_cent));
  const fits = data.shortfall_raw === 0;
  const stamp = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
  status.textContent = (data.source === "saved" ? "Saved Devnet simulation" : "Checked on Devnet") + " · " + stamp + " UTC";
  el("stop-verdict").textContent = fits ? "The reserve covers both." : money(Math.ceil(data.shortfall_raw / data.raw_units_per_cent)) + " more needed.";
  facts(el("stop-breakdown"), [
    ["Available from the reserve", model(data.available_raw)],
    ...(fits ? [["To the fuel company", model(data.vendor_raw)], ["Back to the driver’s wallet", model(data.wallet_increase_raw)]]
      : [["Fuel and road cash requested", money(data.fuel_cents + data.road_cents)]])
  ]);
  result.dataset.covered = String(fits);
  result.dataset.lastCheck = stamp + " UTC";
  facts(el("share-history"), [
    ["From the first payment", tokenUnits(data.first_shares_raw) + " shares"],
    ["From the customer and reserve payment", tokenUnits(data.offer_shares_raw) + " shares"],
    ["Wallet cash after the second payment", tokenUnits(data.wallet_after_settlement_raw) + " test USDC"],
    ["Eligible at this check", tokenUnits(data.eligible_shares_raw) + " shares"]
  ]);
  el("share-check").textContent = "Reserve check at slot " + data.slot.toLocaleString("en-US") + " · " + stamp + " UTC";
  el("stop-network").textContent = "At slot " + data.slot.toLocaleString("en-US") + ", the simulated withdrawal released "
    + tokenUnits(data.available_raw) + " test USDC. " + (fits
      ? "A single transaction preview credited " + tokenUnits(data.vendor_raw) + " to the vendor and added "
        + tokenUnits(data.wallet_increase_raw) + " to the driver’s wallet. The quoted fee was " + data.network_fee_lamports / 1e9 + " test SOL."
      : "That falls short of the two requested amounts. Reduce either amount to check a smaller stop.");
  if (!data.eligible_shares_raw) el("stop-network").textContent = "The driver held no eligible shares at slot " + data.slot.toLocaleString("en-US") + ".";
  const future = data.holdback;
  el("gallon-assumption").hidden = el("stop-adjustment").hidden = !future;
  const download = el("stop-transcript");
  download.href = future ? "/evidence/two-stops" : "/evidence/next-stop";
  download.download = future ? "FreightOS-saved-two-stops.json" : "FreightOS-saved-next-stop.json";
  download.textContent = future ? "Download the original three-gallon simulation" : "Download the original $20 simulation";
  if (future) {
    const verified = data.retained_raw != null;
    const now = model(data.vendor_raw);
    el("stop-verdict").textContent = verified ? now + " for this stop." : "The reserve can’t cover this stop.";
    result.dataset.covered = String(verified);
    const rows = [["Available from both paid bills", model(data.available_raw)]];
    if (verified) {
      rows.push(["To the fuel company", now], ["Back in your wallet", model(data.wallet_increase_raw)],
        ["Still available in Kamino", model(data.retained_raw)]);
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
    el("gallon-method").textContent = "The sample trip’s " + future.trip_gallons + "-gallon estimate was " + money(future.trip_cents)
      + " using Gemini’s $" + Number(future.oil_close).toFixed(2) + " oil close. We applied the same cost per gallon and rounded the budget up to the next cent. Local diesel prices may differ.";
    el("gallon-event").href = "https://api.gemini.com/v1/prediction-markets/events/" + encodeURIComponent(future.event);
  }
  result.hidden = false;
}

async function check(live) {
  const attempt = ++revision;
  button.disabled = true;
  if (live) status.textContent = "Checking the paid receipts, reserve and fuel payment…";
  try {
    const body = live ? { fuel_cents: Math.round(el("stop-fuel").valueAsNumber * 100), road_cents: Math.round(el("stop-road").valueAsNumber * 100) } : undefined;
    const later = el("keep-fuel").checked;
    if (live && later) body.gallons = el("stop-gallons").valueAsNumber;
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
  el("stop-fuel-label").textContent = later ? "Planned fill now" : "Fuel bill";
  el("stop-gallons").disabled = el("stop-later").hidden = !later;
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
updateMode();
check(false);
