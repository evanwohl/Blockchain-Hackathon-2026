import { el, money, request, tokenUnits } from "./common.js";

const check = el("check-payment");
const result = el("result");
function showSteps(proof) {
  const amount = tokenUnits(proof.vendor_received_raw);
  const left = proof.shares_minted_raw - proof.shares_burned_raw;
  el("shares-issued").textContent = tokenUnits(proof.shares_minted_raw);
  el("shares-spent").textContent = "−" + tokenUnits(proof.shares_burned_raw);
  el("shares-left").textContent = tokenUnits(left);
  el("share-ledger").hidden = false;
  el("shares-link").href = "https://explorer.solana.com/address/" + proof.share_account + "?cluster=devnet";
  el("shares-link").hidden = false;
  const rows = [
    ["deposit", "Set aside " + tokenUnits(proof.deposit_raw) + " test USDC", "The driver received Kamino vault shares."],
    ["approval", "Approve the fuel company", "The signed approval fixes the recipient, payment and maximum shares to use."],
    ["payment", "Send " + amount + " test USDC", "The reserve withdrawal and exact vendor payment settled together."]
  ];
  el("stops").replaceChildren(...rows.map(([step, title, description]) => {
    const item = document.createElement("li");
    const link = document.createElement("a");
    link.textContent = title;
    link.href = "https://explorer.solana.com/tx/" + proof.signatures[step] + "?cluster=devnet";
    link.target = "_blank";
    link.rel = "noopener";
    const detail = document.createElement("p");
    detail.textContent = description;
    item.append(link, detail);
    return item;
  }));
}

async function receipt(live) {
  check.disabled = true;
  if (live) result.textContent = "Checking the three signed transactions and the vendor’s balance change…";
  try {
    const proof = await request("/api/public-payment" + (live ? "/verify" : ""));
    showSteps(proof);
    result.textContent = live
      ? "Verified on Devnet. The fuel company received " + tokenUnits(proof.vendor_received_raw) + " test USDC in slot " + proof.slot.toLocaleString("en-US") + "."
      : "Saved receipt. You can check it against the public network.";
  } catch {
    result.textContent = "The receipt check is unavailable. Try again or use Solana Explorer below.";
  } finally {
    check.disabled = false;
  }
}
check.onclick = () => receipt(true);
async function preview(portion) {
  const buttons = [el("preview-road"), el("preview-all")];
  for (const button of buttons) button.disabled = true;
  const status = el("preview-status");
  el("preview-outcome").hidden = true;
  status.textContent = "Checking the public share balance and simulating the return…";
  try {
    const quote = await request("/api/public-payment/return/" + portion);
    const amount = tokenUnits(quote.return_raw) + " test USDC";
    const equivalent = money(quote.return_raw / quote.raw_units_per_cent);
    el("preview-amount").textContent = amount;
    el("preview-value").textContent = equivalent + " at the demo scale."
      + (quote.target_raw && quote.return_raw < quote.target_raw ? " The available shares fall short of the $10 request." : "");
    el("preview-used").textContent = tokenUnits(quote.shares_used_raw);
    el("preview-kept").textContent = tokenUnits(quote.eligible_shares_raw - quote.shares_used_raw);
    el("preview-outcome").hidden = false;
    status.textContent = quote.eligible_shares_raw
      ? "Public preview at slot " + quote.slot.toLocaleString("en-US") + ". The wallet would receive " + amount + "."
      : "The wallet has no remaining shares available from this deposit at slot " + quote.slot.toLocaleString("en-US") + ".";
  } catch {
    status.textContent = "The public quote is unavailable. Retry the preview or check the share account.";
  } finally {
    for (const button of buttons) button.disabled = false;
  }
}

for (const portion of ["road", "all"]) {
  const button = el("preview-" + portion);
  button.disabled = false;
  button.onclick = () => preview(portion);
}
receipt(false);

let loadingFuel = false;
async function fuelComparison() {
  if (loadingFuel || !el("public-fuel-result").hidden) return;
  loadingFuel = true;
  el("fuel-retry").hidden = true;
  el("public-fuel-status").textContent = "Checking the fuel evidence…";
  try {
    const data = await request("/api/public-payment/fuel");
    el("fuel-basis").textContent = "The fuel bill was " + money(data.approved_cents) + ". Early invoice pay covered "
      + money(data.outside_cents) + ", leaving " + money(data.approved_reserve_cents) + " for the reserve.";
    el("fuel-terms").textContent = "Testing $" + data.strike + " oil for " + data.gallons + " gallons on a "
      + money(data.original_cents) + " base budget. Local diesel prices can differ.";
    el("reference-rows").replaceChildren(...[
      ["$" + data.band_floor + " band edge (approved)", data.approved_cents, data.approved_reserve_cents],
      ["$" + Number(data.settlement).toFixed(5) + " Gemini settlement", data.estimate_cents, data.reserve_cents]
    ].map(([label, bill, reserve]) => {
      const row = document.createElement("tr");
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.textContent = label;
      row.append(heading);
      row.insertCell().textContent = money(bill);
      row.insertCell().textContent = money(reserve);
      return row;
    }));
    el("try-settlement").href = "/bill#" + new URLSearchParams({ plan: data.profile, fuel: data.scenario });
    el("public-fuel-status").textContent = "Recorded market inputs match the hashes in the signed bill.";
    el("public-fuel-result").hidden = false;
  } catch {
    el("public-fuel-status").textContent = "Could not check the fuel inputs against the bill.";
    el("fuel-retry").hidden = false;
  } finally {
    loadingFuel = false;
  }
}
el("public-fuel").ontoggle = () => { if (el("public-fuel").open) fuelComparison(); };
el("fuel-retry").onclick = fuelComparison;
