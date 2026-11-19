import { downloadReceipt, el, facts, money, request, tokenUnits } from "./common.js";

const profiles = {
  "quick pay": { source: "Early payment on your backhaul invoice", fallback: "Quick pay on $3,000 invoice", terms: "Quick pay releases your invoice early for a 5% fee." },
  "credit line": { source: "From your bank credit line", fallback: "Credit line alone", terms: "The sample credit line offers $800 at 1% for the term." },
  "cash on hand": { source: "From cash on hand", fallback: "Paying from cash", terms: "Available cash covers the bill by its due date." }
};
let state;
let pending = "";
let expiryTimer;
let currentStep;

function renderPlan(plan) {
  const profile = profiles[plan.profile];
  el("saved").textContent = money(plan.savings_cents);
  el("plan-cost").textContent = money(plan.cost_cents);
  el("alternative").textContent = profile.fallback;
  el("other-cost").textContent = money(plan.alternative_cost_cents);
  const highestCost = Math.max(plan.cost_cents, plan.alternative_cost_cents, 1);
  el("plan-bar").style.width = (100 * plan.cost_cents / highestCost) + "%";
  el("other-bar").style.width = (100 * plan.alternative_cost_cents / highestCost) + "%";
  el("bill").textContent = money(plan.bill_cents);
  el("amount").textContent = money(plan.payment_cents);
  el("other").textContent = money(plan.other_funds_cents);
  el("other-label").textContent = profile.source;
  el("bar").style.width = (100 * plan.payment_cents / plan.bill_cents) + "%";
  const costs = plan.funding.map(item => [
    item.source === "reserve redemption" ? "Using the reserve" : "Using bank credit",
    money(item.cost_cents)
  ]);
  for (const payment of plan.invoice_elections) {
    const invoice = plan.calendar.invoices.find(item => item.id === payment.invoice);
    const fee = invoice.face_cents - payment.receipt_cents;
    if (fee) costs.push(["Early payment on the " + payment.invoice + " invoice", money(fee)]);
  }
  costs.push(["Total estimated cost", money(plan.cost_cents)]);
  facts(el("fees"), costs);
  el("terms").textContent = profile.terms + " Reserve costs include the sample fixed charge and estimated interest given up.";
  const g = plan.gemini;
  el("signal").textContent = "Gemini’s previous oil settlement was at least $" + g.lower_inclusive + " and below $" + g.upper_exclusive + " a barrel. At the recorded $" + g.stress_level + " threshold, the sample estimates up to " + money(g.extra_fuel_cents_interval[1]) + " extra for " + g.gallons + " gallons. This is a stress scenario.";
}

function paymentView(plan) {
  const { receipt, review } = state;
  if (receipt) return {
    stage: 2, title: "Reserve payment delivered", button: "Start another demo",
    description: "The approved vendor received the reserve portion. This load’s reserve payment is recorded as settled.",
    rows: [["Sent to the fuel vendor", tokenUnits(receipt.vendor_received_usdc_raw) + " USDC"], ["Covered separately in the plan", money(plan.other_funds_cents)]]
  };
  if (!plan.payment_cents) return {
    stage: -1, title: "Pay from available cash", button: "No reserve payment needed",
    description: "You have enough cash to pay the vendor directly. Your reserve can stay where it is.",
    rows: [["Cash needed", money(plan.bill_cents)], ["Financing cost", money(plan.cost_cents)]]
  };
  if (review) return {
    stage: 1, title: "Review your payment", button: "Pay " + money(plan.payment_cents) + " in test USDC",
    description: "Check the recipient and amount. The review is valid for up to one minute. You can cancel before paying.",
    rows: [["To", "Fuel vendor · " + review.payee_account.slice(0, 4) + "…" + review.payee_account.slice(-4)], ["Vendor receives", tokenUnits(review.usdc_raw) + " USDC"], ["Maximum reserve shares used", tokenUnits(review.max_shares_raw)], ["Payment network fee", review.network_fee_lamports / 1e9 + " SOL"]]
  };
  if (state.deposit) return {
    stage: 1, title: "Your reserve is ready", button: "Review " + money(plan.payment_cents) + " payment",
    description: "Confirm who will receive the funds and how much to send.",
    rows: [["From your reserve", money(plan.payment_cents)], ["Covered separately", money(plan.other_funds_cents)]]
  };
  const amount = tokenUnits(state.wallet_usdc_raw);
  return {
    stage: 0, title: "Fund your reserve", button: "Deposit " + amount + " test USDC",
    description: "Set aside test USDC (digital dollars) in Kamino. Review the vendor before paying.",
    rows: [["Available in demo wallet", amount + " USDC"]]
  };
}

function render() {
  if (!state) return;
  const plan = state.funding_plans[el("profile").value][el("scenario").value];
  renderPlan(plan);
  const { review, receipt } = state;
  const view = paymentView(plan);
  const busy = Boolean(pending);
  el("profile").disabled = el("scenario").disabled = busy || Boolean(review || receipt);
  el("title").textContent = view.title;
  el("instruction").textContent = view.description;
  facts(el("facts"), view.rows);
  el("steps").hidden = view.stage < 0;
  Array.from(el("steps").children).forEach((step, index) => {
    if (index === view.stage) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
  });
  el("primary").disabled = busy || !plan.payment_cents;
  el("primary").textContent = pending || view.button;
  el("cancel").hidden = el("challenge").hidden = !review;
  el("verify").hidden = el("download").hidden = !receipt;
  el("verify").disabled = el("cancel").disabled = el("check").disabled = busy;
  if (!review) {
    el("result").textContent = "";
    el("challenge").open = false;
  }
  if (!receipt) el("verified").textContent = "";
  currentStep = receipt || review || state.deposit;
  el("proof").hidden = !currentStep;
  if (el("proof").open) el("details").textContent = JSON.stringify(currentStep, null, 2);
}

async function sync() {
  state = await request("/api/state");
  const locked = state.receipt || state.review;
  if (locked) {
    el("scenario").value = locked.decision.scenario;
    el("profile").value = locked.decision.profile;
  }
  clearTimeout(expiryTimer);
  if (state.review) expiryTimer = setTimeout(async () => {
    if (pending) return;
    try {
      await sync();
      if (!state.review && !state.receipt) el("status").textContent = "Your review expired. Review the payment again to continue.";
    } catch (error) { el("status").textContent = error.message; }
  }, Math.ceil(state.review.expires_in_seconds * 1000) + 100);
  render();
}

async function run(label, action) {
  if (pending) return;
  pending = label;
  el("status").textContent = "";
  render();
  try {
    await action();
  } catch (error) {
    el("status").textContent = error.message;
  } finally {
    try { await sync(); } catch (error) {
      state = null;
      showRetry(error);
    }
    pending = "";
    render();
  }
}

function showRetry(error) {
  el("status").textContent = error.message;
  el("title").textContent = "Reconnect to your payment";
  el("primary").disabled = false;
  el("primary").textContent = "Reload payment";
  el("profile").disabled = el("scenario").disabled = true;
  el("cancel").hidden = el("challenge").hidden = el("verify").hidden = el("download").hidden = true;
}

el("primary").onclick = () => {
  if (!state) return location.reload();
  if (state.receipt) return run("Starting over…", () => request("/api/reset", {}));
  if (!state.deposit) return run("Funding reserve…", () => request("/api/deposit", {}));
  if (state.review) return run("Sending payment…", () => request("/api/approve", { approval: state.review.id }));
  return run("Preparing review…", () => request("/api/review", { profile: el("profile").value, scenario: el("scenario").value }));
};
el("cancel").onclick = () => run("Cancelling…", () => request("/api/cancel", { approval: state.review.id }));
el("check").onclick = () => run("Checking recipient…", async () => {
  const result = await request("/api/check-vendor", { approval: state.review.id });
  el("result").textContent = "Payment blocked. The other wallet received " + tokenUnits(result.other_vendor_usdc_raw) + " USDC.";
});
el("verify").onclick = () => run("Verifying…", async () => {
  const proof = await request("/api/verify", {});
  el("verified").textContent = proof.verified_signatures + " signatures verified. An independent replay confirmed delivery of " + tokenUnits(proof.vendor_received_usdc_raw) + " USDC.";
});
el("download").onclick = () => downloadReceipt(state.load, state.receipt);
el("scenario").onchange = el("profile").onchange = render;
el("proof").ontoggle = () => { el("details").textContent = el("proof").open ? JSON.stringify(currentStep, null, 2) : ""; };
sync().catch(showRetry);
