import { el, facts, money, request, tokenUnits } from "./common.js";

const verify = el("verify-offer");
const status = el("offer-status");
el("offer-trace").open = matchMedia("(min-width: 761px)").matches;

function renderComparison(comparison, decision) {
  const labels = [["customer_fee_cents", "Customer fee"], ["invoice_fees_cents", "Freight invoice discounts"], ["reserve_cost_cents", "Fuel reserve cost"]];
  el("cost-proof-rows").replaceChildren(...labels.map(([key, name]) => {
    const row = document.createElement("tr");
    const header = document.createElement("th");
    header.scope = "row";
    header.textContent = name;
    row.append(header);
    row.insertCell().textContent = money(comparison.early[key]);
    row.insertCell().textContent = money(comparison.wait[key]);
    return row;
  }));
  el("cost-proof-draw").textContent = "Taking the offer uses " + money(comparison.early_draw_cents) + " from the reserve. Waiting uses "
    + money(comparison.wait_draw_cents) + " and requires early pay on the return load. Both estimates use the same reserve pricing. Network fees are excluded.";
  el("cost-proof-limits").textContent = "The driver planned up to " + money(comparison.planned_cents) + " of reserve withdrawals for the six fuel estimates. With that limit, the highest fee worth paying is "
    + money(comparison.planned_fee_ceiling_cents) + ". Allowing up to " + money(comparison.available_cents) + " raises the fee ceiling to " + money(comparison.available_fee_ceiling_cents) + ".";
  el("try-reserve-limit").href = "/bill#" + new URLSearchParams({ plan: "quick pay", fuel: decision.scenario, view: "surcharge-offer",
    offer_fee_cents: "800", reserve_limit_cents: String(comparison.planned_cents) });
  el("cost-proof").hidden = false;
}

function render(proof) {
  const d = proof.decision;
  const units = raw => (raw / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 }) + " test USDC";
  el("full-payment").textContent = tokenUnits(proof.received_raw);
  facts(el("offer-costs"), [["Estimated with early pay", money(d.fees_cents)], ["Estimated if the driver waits", money(d.wait_cost_cents)]]);
  el("offer-costs").hidden = false;
  el("offer-savings").textContent = "That saves " + money(d.wait_cost_cents - d.fees_cents) + " in modeled fees on this bill. The engine compared both plans across " + d.budgets + " Gemini fuel estimates.";
  renderComparison(proof.cost_comparison, d);
  facts(el("settled-sources"), [["From the customer, through the driver", units(proof.customer_paid_raw)], ["From the driver’s Kamino reserve", units(proof.reserve_paid_raw)]]);
  el("settled-bar").style.setProperty("--customer-share", 100 * proof.customer_paid_raw / proof.received_raw + "%");
  el("settled-split").hidden = false;
  const steps = [
    ["prepare", "Set aside funds and approve the vendor", "The demo driver funded the reserve and the customer wallet. The approval fixed the fuel company, amount and offer terms."],
    ["settle", "Both wallets signed the fuel payment", "The customer kept " + units(proof.customer_kept_raw) + " and sent the rest to the driver. The driver forwarded it and paid the balance from Kamino. All transfers settled together."]
  ];
  el("offer-stops").replaceChildren(...steps.map(([step, title, text]) => {
    const row = document.createElement("li");
    const link = document.createElement("a");
    link.href = "https://explorer.solana.com/tx/" + proof.steps[step].signature + "?cluster=devnet";
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = title;
    const description = document.createElement("p");
    description.textContent = text;
    row.append(link, description);
    return row;
  }));
}

async function check(live) {
  verify.disabled = true;
  if (live) status.textContent = "Checking both signatures, the reserve withdrawal and the vendor’s balance…";
  try {
    const proof = await request("/api/public-offer" + (live ? "/verify" : ""));
    render(proof);
    status.textContent = live ? "Verified on Devnet. The vendor received " + tokenUnits(proof.received_raw)
      + " test USDC in slot " + proof.steps.settle.slot.toLocaleString("en-US") + "." : "Saved receipt. Verify it against the public network.";
  } catch {
    status.textContent = "The receipt check is unavailable. Try again or open the signed transaction below.";
  } finally {
    verify.disabled = false;
  }
}
verify.onclick = () => check(true);
check(false);

const refreshFuel = el("refresh-fuel");
const utcDate = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
const utc = date => utcDate.format(new Date(date)) + " UTC";

function renderFuel(fuel) {
  el("margin-label").textContent = fuel.headroom_cents >= 0 ? "Room in the paid budget" : "Estimated amount over budget";
  el("margin-value").textContent = money(Math.abs(fuel.headroom_cents));
  el("fuel-margin").hidden = false;
  facts(el("fuel-update-facts"), [
    ["Bill already paid", money(fuel.paid_cents)],
    ["Updated fuel estimate", money(fuel.marked_cents)],
    ["Customer contribution", money(fuel.customer_cents)],
    ["Reserve needed at this estimate", money(fuel.reserve_needed_cents)]
  ]);
  el("fuel-at").textContent = (fuel.source === "saved" ? "Saved check" : "Checked with Gemini") + " · " + utc(fuel.checked_at);
  const closeDate = fuel.close_at ? " on " + utc(fuel.close_at) : " at approval";
  el("fuel-method").textContent = "For this " + fuel.gallons + "-gallon trip, the model adjusts the original " + money(fuel.base_cents)
    + " estimate by " + money(fuel.cents_per_oil_dollar) + " for each $1 change in a barrel of oil. Gemini reported $" + Number(fuel.close).toFixed(5) + closeDate
    + ". At $" + fuel.first_uncovered_oil + " a barrel, the modeled bill first exceeds the paid amount. Actual diesel prices can differ.";
  const condition = fuel.target_outcome ? "Settled " + fuel.target_outcome.toUpperCase() : fuel.yes_ask != null
    ? "YES was offered at $" + Number(fuel.yes_ask).toFixed(2) + " per contract" : "No current YES offer was verified";
  el("fuel-quote").textContent = condition + " at the check for the $" + fuel.threshold + " oil threshold expiring " + utc(fuel.expiry)
    + ". FreightOS used this market as a reference. The demo bought no contracts.";
  const link = el("fuel-event");
  link.href = "https://api.gemini.com/v1/prediction-markets/events/" + encodeURIComponent(fuel.close_ticker);
  link.hidden = false;
}

async function checkFuel(live) {
  refreshFuel.disabled = true;
  if (live) el("fuel-status").textContent = "Checking the oil markets from this trip…";
  try {
    renderFuel(await request("/api/public-offer/fuel" + (live ? "/refresh" : "")));
    el("fuel-status").textContent = "The fuel company’s payment is final. This check updates the trip estimate.";
  } catch {
    el("fuel-status").textContent = el("fuel-margin").hidden ? "Fuel estimate unavailable. Try refreshing."
      : "Gemini could not be checked. Showing the last successful estimate.";
  } finally {
    refreshFuel.disabled = false;
  }
}
refreshFuel.onclick = () => checkFuel(true);
checkFuel(false);
