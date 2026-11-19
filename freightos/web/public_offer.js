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
