const el = id => document.getElementById(id);
const money = cents => (cents / 100).toLocaleString("en-US", {style: "currency", currency: "USD"});
const units = raw => (raw / 1e6).toLocaleString("en-US", {maximumFractionDigits: 6});
const plans = { "quick pay": "Backhaul quick pay", "credit line": "Approved bank line", "cash on hand": "Available cash" };
let state, review, busy = false;

async function api(path, body) {
  const options = body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {};
  const response = await fetch("/api/" + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}

function showFacts(rows) {
  el("facts").replaceChildren(...rows.map(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    return row;
  }));
}

function render() {
  const receipt = state.receipt;
  const plan = state.funding_plans[el("profile").value][el("scenario").value];
  el("bill").textContent = money(plan.bill_cents);
  el("amount").textContent = money(plan.payment_cents);
  el("other").textContent = money(plan.other_funds_cents);
  el("other-label").textContent = plans[plan.profile];
  el("bar").style.width = (100 * plan.payment_cents / plan.bill_cents) + "%";
  el("cost").textContent = money(plan.cost_cents);
  el("alt-cost").textContent = money(plan.alternative_cost_cents);
  el("saved").textContent = money(plan.savings_cents);
  el("alternative").textContent = {"quick pay":"Quick pay on $3,000 invoice", "credit line":"Credit line alone", "cash on hand":"Pay from cash"}[plan.profile];
  el("terms").textContent = {"quick pay":"Assumes 5% one-day quick pay and a USDC-accepting vendor.", "credit line":"Assumes an $800 credit line at 1% for the term, with no fixed fee.", "cash on hand":"Available operating cash covers the bill before its due date."}[plan.profile];
  const g = plan.gemini;
  el("signal").textContent = g.stress_contract + " · The prior WTI fixing was at least $" + g.lower_inclusive + " and below $" + g.upper_exclusive + ". At $" + g.stress_level + ", the assumed pass-through on " + g.gallons + " gallons adds up to " + money(g.extra_fuel_cents_interval[1]) + ". The stress scenario uses that upper estimate.";
  el("profile").disabled = el("scenario").disabled = busy || !!review || !!receipt;
  el("cancel").hidden = !review || !!receipt;
  el("cancel").disabled = el("check").disabled = busy;
  el("challenge").hidden = !review || !!receipt;
  el("download").hidden = !receipt;
  el("verify").hidden = !receipt;
  el("verify").disabled = busy;
  if (!receipt) el("verified").textContent = "";
  el("proof").hidden = !review && !receipt && !state.deposit;
  el("details").textContent = JSON.stringify(receipt || review || state.deposit, null, 2);
  const stage = receipt || review ? 2 : state.deposit ? 1 : 0;
  [...el("steps").children].forEach((step, i) => i === stage ? step.setAttribute("aria-current", "step") : step.removeAttribute("aria-current"));
  el("primary").disabled = busy || !plan.payment_cents;
  if (receipt) {
    el("title").textContent = "Vendor payment settled";
    el("instruction").textContent = "The approved vendor received USDC. The FreightOS bill record now prevents a second guarded settlement for this load.";
    showFacts([["Vendor received", units(receipt.vendor_received_usdc_raw) + " USDC"], ["Vault shares redeemed", units(receipt.vault_shares_burned_raw)], ["Reserve shares left", units(receipt.remaining_vault_shares_raw)], ["Bill status", "Settled on fork"]]);
    el("primary").textContent = "Start another demo";
  } else if (!plan.payment_cents) {
    el("title").textContent = "Keep your reserve invested";
    el("instruction").textContent = "Pay the vendor from available cash and collect both freight invoices on standard terms.";
    showFacts([["Reserve withdrawal", "$0.00"], ["Financing cost", "$0.00"]]);
    el("primary").textContent = "No reserve payment needed";
  } else if (review) {
    el("title").textContent = "Approve vendor payment";
    el("instruction").textContent = "The bill authorization binds the vendor, the amount and your maximum share burn. Approve to redeem and pay.";
    showFacts([["Vendor receives", units(review.usdc_raw) + " USDC"], ["Maximum vault shares", units(review.max_shares_raw)], ["Payment network fee", review.network_fee_lamports + " lamports"], ["Vendor USDC account", review.payee_account.slice(0, 6) + "…" + review.payee_account.slice(-6)]]);
    el("primary").textContent = "Approve payment";
  } else if (state.deposit) {
    el("title").textContent = "Reserve funded";
    el("instruction").textContent = "Review locks the bill terms on the fork. You can cancel before paying.";
    showFacts([["Reserve shares", units(state.reserve_shares_raw)], ["Payment to vendor", money(plan.payment_cents)]]);
    el("primary").textContent = "Review vendor payment";
  } else {
    el("title").textContent = "Fund your reserve";
    el("instruction").textContent = "Deposit test USDC into Kamino. Your demo wallet receives lending vault shares.";
    showFacts([["Demo wallet", units(state.wallet_usdc_raw) + " USDC"], ["Reserve shares", units(state.reserve_shares_raw)]]);
    el("primary").textContent = "Deposit 600 test USDC";
  }
}

async function run(action) {
  if (busy) return;
  busy = true;
  el("status").textContent = "";
  render();
  try {
    await action();
  } catch (error) {
    el("status").textContent = error.message;
    if (error.message.includes("expired")) review = null;
  } finally {
    try { state = await api("state"); } catch (error) { el("status").textContent = error.message; }
    if (state.receipt) {
      review = null;
      el("scenario").value = state.receipt.decision.scenario;
      el("profile").value = state.receipt.decision.profile;
    }
    busy = false;
    render();
  }
}

el("scenario").onchange = el("profile").onchange = render;
el("cancel").onclick = () => run(async () => {
  await api("cancel", {approval: review.id});
  review = null;
});
el("check").onclick = () => run(async () => {
  const result = await api("check-vendor", {approval: review.id});
  el("result").textContent = "Rejected by FreightOS. The alternate vendor received " + units(result.other_vendor_usdc_raw) + " USDC. Your original approval is still valid.";
});
el("primary").onclick = () => run(async () => {
  el("result").textContent = "";
  if (state.receipt) await api("reset", {});
  else if (!state.deposit) await api("deposit", {});
  else if (!review) review = await api("review", {scenario: el("scenario").value, profile: el("profile").value});
  else await api("approve", {approval: review.id});
});
el("download").onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(state.receipt, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = state.load + "-settlement.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
el("verify").onclick = () => run(async () => {
  const proof = await api("verify", {});
  el("verified").textContent = proof.verified_signatures + " signatures verified. Replaying the signed transactions reproduced the " + units(proof.vendor_received_usdc_raw) + " USDC payment and the settled bill.";
});
api("state").then(s => {
  state = s;
  if (s.receipt) {
    el("scenario").value = s.receipt.decision.scenario;
    el("profile").value = s.receipt.decision.profile;
  }
  render();
}).catch(error => { el("status").textContent = error.message; });
