import { el, request, tokenUnits } from "./common.js";

const check = el("check-payment");
const result = el("result");
function showSteps(proof) {
  const amount = tokenUnits(proof.vendor_received_raw);
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
receipt(false);
