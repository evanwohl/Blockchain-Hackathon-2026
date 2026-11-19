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
  result.hidden = false;
}

async function check(live) {
  const attempt = ++revision;
  button.disabled = true;
  if (live) status.textContent = "Checking the paid receipts, reserve and fuel payment…";
  try {
    const body = live ? { fuel_cents: Math.round(el("stop-fuel").valueAsNumber * 100), road_cents: Math.round(el("stop-road").valueAsNumber * 100) } : undefined;
    const data = await request("/api/next-stop", body, AbortSignal.timeout(45000));
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
form.oninput = () => {
  revision++;
  result.hidden = true;
  button.disabled = false;
  status.textContent = "Check these amounts for an updated preview.";
};
form.onsubmit = event => { event.preventDefault(); check(true); };
check(false);
