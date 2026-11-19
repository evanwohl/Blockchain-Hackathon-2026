import { el, facts, money, request, tokenUnits } from "./common.js";

const verify = el("verify-offer");
const status = el("offer-status");
el("offer-trace").open = matchMedia("(min-width: 761px)").matches;

function render(proof) {
  const d = proof.decision;
  const units = raw => (raw / 1e6).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 6 }) + " test USDC";
  el("full-payment").textContent = tokenUnits(proof.received_raw);
  facts(el("offer-costs"), [["Estimated with early pay", money(d.fees_cents)], ["Estimated if the driver waits", money(d.wait_cost_cents)]]);
  el("offer-costs").hidden = false;
  el("offer-savings").textContent = "That saves " + money(d.wait_cost_cents - d.fees_cents) + " in modeled fees on this bill. The highest fee worth paying across the "
    + d.budgets + " Gemini fuel estimates was " + money(d.highest_fee_cents) + ".";
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
