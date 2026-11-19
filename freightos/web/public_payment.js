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
