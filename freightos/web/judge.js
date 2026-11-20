import { el, facts, money, request, tokenUnits } from "./common.js";

let proof;

function renderPayment(result) {
  const d = result.decision;
  el("judge-received").textContent = tokenUnits(result.received_raw);
  facts(el("judge-sources"), [
    ["Customer → driver → vendor", tokenUnits(result.customer_paid_raw) + " test USDC"],
    ["Kamino reserve → vendor", tokenUnits(result.reserve_paid_raw) + " test USDC"],
    ["Modeled fuel bill", money(d.bill_cents)]
  ]);
  el("judge-proof-source").textContent = (result.source === "public-rpc" ? "Verified against public Devnet" : "Verified saved signed evidence")
    + " · Settlement slot " + result.steps.settle.slot.toLocaleString("en-US") + ".";
  el("judge-choice").textContent = "Pay " + money(d.bill_cents) + " for fuel on day " + d.surcharge.day
    + ". Taking the customer’s " + money(d.surcharge.cents) + " surcharge early costs " + money(d.surcharge.fee_cents)
    + " and releases " + money(d.outside_cents) + ". The reserve sends " + money(d.reserve_cents)
    + "; both freight invoices stay on regular terms.";
  facts(el("judge-costs"), [["Take the customer offer", money(d.fees_cents)],
    ["Wait for the surcharge", money(d.wait_cost_cents)],
    ["Modeled fee reduction", money(d.wait_cost_cents - d.fees_cents)]]);
  el("judge-fee-scope").textContent = "Same bill and funding options, checked across " + d.budgets
    + " fuel budgets. Estimated funding costs exclude Solana network fees.";
  el("judge-practice").href = "/bill#" + new URLSearchParams({ plan: "quick pay", fuel: d.scenario,
    view: "surcharge-offer", surcharge_cents: d.surcharge.cents, offer_fee_cents: d.surcharge.fee_cents,
    reserve_limit_cents: result.cost_comparison.planned_cents });
}

async function checkPayment(live) {
  el("judge-verify").disabled = el("judge-retry").disabled = true;
  el("judge-proof-status").textContent = live
    ? "Checking finalized signatures, both contributions and the vendor’s balance…" : "Checking the saved signed receipt…";
  try {
    const result = await request("/api/public-offer" + (live ? "/verify" : ""));
    renderPayment(result);
    proof = result;
    el("judge-retry").hidden = true;
    el("judge-proof-status").textContent = live ? "Public payment verified. Both contributions settled in the same transaction."
      : "Saved proof checked. Use the public check to retrieve finalized network records.";
  } catch {
    el("judge-proof-status").textContent = proof
      ? "Recheck unavailable. The last verified result remains above. You can retry or open the full receipt."
      : "The payment could not be checked. Reload the saved evidence or open the full receipt.";
    el("judge-retry").hidden = !!proof;
  } finally {
    el("judge-verify").disabled = el("judge-retry").disabled = false;
  }
}

async function checkTrial() {
  el("judge-trial-retry").disabled = true;
  el("judge-trial-status").textContent = "Opening the same-reserve comparison…";
  try {
    const trial = await request("/api/trial");
    const edge = trial.composition_savings;
    el("judge-trial-result").textContent = money(edge.total_cents) + " lower modeled fees across " + trial.cases + " synthetic loads.";
    el("judge-trial").textContent = "FreightOS cost less on " + edge.positive_cases + " loads; " + edge.ties
      + " tied. Both strategies had the same reserve. The baseline takes the cheaper of conventional funding and reserve-plus-invoices funding.";
    el("judge-trial-status").textContent = "Generated terms; no claim of realized savings. An identical manual optimization ties FreightOS.";
    el("judge-trial-retry").hidden = true;
  } catch {
    el("judge-trial-status").textContent = "Comparison unavailable. Retry or inspect the load results.";
    el("judge-trial-retry").hidden = false;
  } finally {
    el("judge-trial-retry").disabled = false;
  }
}

el("judge-verify").onclick = () => checkPayment(true);
el("judge-retry").onclick = () => checkPayment(false);
el("judge-trial-retry").onclick = checkTrial;
checkPayment(false);
checkTrial();
