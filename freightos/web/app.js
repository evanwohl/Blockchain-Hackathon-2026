import { downloadReceipt, el, facts, money, request, tokenUnits } from "./common.js";

const profiles = {
  "quick pay": { source: "Early backhaul payment", comparison: "invoice quick pay", fallback: "Quick pay on $3,000 invoice", terms: "Quick pay releases your invoice early for a 5% fee." },
  "credit line": { source: "Bank credit line", comparison: "using bank credit alone", fallback: "Credit line alone", terms: "The sample credit line offers $800 at 1% for the term." },
  "cash on hand": { source: "Available cash", comparison: "paying from cash", fallback: "Paying from cash", terms: "Available cash covers the bill by its due date." }
};
let state;
let pending = "";
let expiryTimer;
let clockTimer;
let currentStep;

let profile = sessionStorage.getItem("funding-profile") || "quick pay";
let scenario = sessionStorage.getItem("fuel-scenario") || "oil stress";

function renderCalendar(plan) {
  const bill = plan.calendar.bills[0];
  const primary = plan.invoice_elections.find(item => item.invoice === "primary load");
  el("load-id").textContent = plan.load;
  el("bill-due").textContent = "Day " + bill.day;
  el("invoice-value").textContent = money(primary.receipt_cents);
  el("invoice-day").textContent = "Day " + primary.day;
  const gap = primary.day - bill.day;
  el("timing").textContent = gap === 0 ? "The fuel bill and freight payment fall on the same day."
    : "Fuel is due " + Math.abs(gap) + (Math.abs(gap) === 1 ? " day " : " days ") + (gap > 0 ? "before" : "after") + " the freight invoice pays.";
  const days = Math.max(primary.day, bill.day, 1);
  const strip = el("day-strip");
  const ticks = document.createDocumentFragment();
  for (let day = 1; day <= days; day++) {
    const tick = document.createElement("i");
    if (day >= Math.min(bill.day, primary.day) && day <= Math.max(bill.day, primary.day)) tick.className = "between";
    if (day === bill.day) tick.classList.add("due-day");
    if (day === primary.day) tick.classList.add("invoice-day");
    ticks.append(tick);
  }
  strip.replaceChildren(ticks);
  const events = [
    { day: bill.day, label: "Fuel bill due", note: "Vendor payment deadline", amount: bill.cents, bill: true },
    ...plan.invoice_elections.map(item => ({
      day: item.day, label: item.invoice === "backhaul" ? "Backhaul invoice" : "Freight invoice",
      note: item.option === "standard" ? "Scheduled payment" : "Early payment", amount: item.receipt_cents
    })),
    ...plan.funding.map(item => ({
      day: plan.calendar.funding.find(source => source.id === item.source).available_day,
      label: item.source === "reserve redemption" ? "Reserve available" : "Bank credit available",
      note: "Planned funding", amount: item.net_proceeds_cents
    }))
  ];
  el("timeline").replaceChildren(...events.sort((a, b) => a.day - b.day).map(event => {
    const row = document.createElement("li");
    const day = document.createElement("span");
    const pin = document.createElement("i");
    const label = document.createElement("span");
    const note = document.createElement("small");
    const amount = document.createElement("strong");
    day.className = "day";
    pin.className = "pin";
    label.className = "event-label";
    day.textContent = "Day " + event.day;
    label.textContent = event.label;
    note.textContent = event.note;
    amount.textContent = money(event.amount);
    if (event.bill) row.dataset.bill = "";
    label.append(note);
    row.append(day, pin, label, amount);
    return row;
  }));
}

function renderPlan(plan) {
  const profile = profiles[plan.profile];
  renderCalendar(plan);
  el("saved").textContent = money(plan.savings_cents);
  el("plan-cost").textContent = money(plan.cost_cents);
  el("alternative").textContent = profile.fallback;
  el("other-cost").textContent = money(plan.alternative_cost_cents);
  const highestCost = Math.max(plan.cost_cents, plan.alternative_cost_cents, 1);
  el("plan-bar").style.width = (100 * plan.cost_cents / highestCost) + "%";
  el("other-bar").style.width = (100 * plan.alternative_cost_cents / highestCost) + "%";
  const [dollars, cents] = money(plan.bill_cents).split(".");
  const fraction = document.createElement("small");
  fraction.textContent = "." + cents;
  el("bill").replaceChildren(dollars, fraction);
  el("amount").textContent = money(plan.payment_cents);
  el("other").textContent = money(plan.other_funds_cents);
  el("other-label").textContent = profile.source;
  el("reserve-source").hidden = el("funding-bar").hidden = !plan.payment_cents;
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
  el("market-assumption").textContent = plan.scenario === "oil stress"
    ? g.gallons + " gallons · Higher price estimate"
    : g.gallons + " gallons · Original estimate";
  el("signal").textContent = "Gemini’s previous oil settlement was at least $" + g.lower_inclusive + " and below $" + g.upper_exclusive + " a barrel. At the recorded $" + g.stress_level + " threshold, the sample estimates up to " + money(g.extra_fuel_cents_interval[1]) + " extra for " + g.gallons + " gallons. This is a stress scenario.";
}

function paymentView(plan) {
  const { receipt, review } = state;
  if (receipt) return {
    stage: 2, title: "Reserve payment delivered", button: "Start another demo",
    description: "Your fuel vendor received the reserve payment in test USDC.",
    rows: [["Sent to the fuel vendor", tokenUnits(receipt.vendor_received_usdc_raw) + " USDC"], ["Planned from other funds", money(plan.other_funds_cents)]]
  };
  if (!plan.payment_cents) return {
    stage: -1, title: "Pay from available cash", button: "Done",
    description: "Pay the vendor directly from your available cash.",
    rows: [["Cash needed", money(plan.bill_cents)], ["Financing cost", money(plan.cost_cents)]]
  };
  if (review) return {
    stage: 1, title: "Review your payment", button: "Pay " + money(plan.payment_cents) + " in test USDC",
    description: "Check the recipient and amount before sending.",
    rows: [["To", "Fuel vendor · " + review.payee_account.slice(0, 4) + "…" + review.payee_account.slice(-4)], ["Vendor receives", tokenUnits(review.usdc_raw) + " USDC"], ["Still to cover separately", money(plan.other_funds_cents)], ["Network fee", review.network_fee_lamports / 1e9 + " SOL"]]
  };
  if (state.deposit) return {
    stage: 1, title: "Your reserve is ready", button: "Review " + money(plan.payment_cents) + " payment",
    description: "Confirm who will receive the funds and how much to send.",
    rows: [["From your reserve", money(plan.payment_cents)], ["Covered separately", money(plan.other_funds_cents)]]
  };
  const amount = tokenUnits(state.wallet_usdc_raw);
  return {
    stage: 0, title: "Set aside demo funds", button: "Set aside $" + amount + " and review",
    description: "Move the demo balance into your reserve. You’ll check the vendor and approve the payment on the next screen.",
    rows: [["Move to your reserve", "$" + amount], ["For this fuel bill", money(plan.payment_cents)]]
  };
}

function render() {
  if (!state) return;
  const plan = state.funding_plans[profile][scenario];
  renderPlan(plan);
  const { review, receipt } = state;
  const view = paymentView(plan);
  const busy = Boolean(pending);
  el("configure").disabled = busy || Boolean(review || receipt);
  el("open-payment").disabled = busy;
  el("open-label").textContent = receipt ? "View receipt" : review ? "Return to review" : plan.payment_cents ? "Review " + money(plan.payment_cents) + " reserve payment" : "View cash plan";
  el("fund-caption").textContent = receipt ? "Reserve payment delivered" : review ? "Ready for your approval" : plan.cost_cents ? money(plan.cost_cents) + " in estimated fees" : "No financing fees";
  el("fee-saving").hidden = Boolean(receipt || review || !plan.savings_cents);
  el("fee-saving").textContent = "Save " + money(plan.savings_cents) + " compared with " + profiles[profile].comparison + ".";
  el("receipt-head").hidden = !receipt;
  el("receipt-load").textContent = receipt ? plan.load : "";
  el("reset").hidden = !receipt;
  el("reset").disabled = busy;
  el("verify").textContent = pending === "Verifying…" ? pending : "Verify payment";
  el("primary").hidden = Boolean(receipt);
  const changedStep = el("title").textContent !== view.title;
  el("title").textContent = view.title;
  if (changedStep && el("payment").open) el("title").focus();
  el("instruction").textContent = view.description;
  facts(el("facts"), view.rows);
  el("steps").hidden = view.stage < 0;
  Array.from(el("steps").children).forEach((step, index) => {
    if (index === view.stage) step.setAttribute("aria-current", "step");
    else step.removeAttribute("aria-current");
  });
  el("primary").disabled = busy;
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
  el("proof").hidden = !plan.payment_cents;
  el("details").hidden = !currentStep;
  if (el("proof").open && currentStep) el("details").textContent = JSON.stringify(currentStep, null, 2);
}

async function sync(restore = false) {
  state = await request("/api/state");
  const locked = state.receipt || state.review;
  if (locked) {
    scenario = locked.decision.scenario;
    profile = locked.decision.profile;
  }
  if (!Object.hasOwn(state.funding_plans, profile)) profile = "quick pay";
  if (!Object.hasOwn(state.funding_plans[profile], scenario)) scenario = "oil stress";
  clearTimeout(expiryTimer);
  clearInterval(clockTimer);
  el("review-clock").hidden = !state.review;
  if (state.review) {
    const expiresAt = Date.now() + state.review.expires_in_seconds * 1000;
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((expiresAt - Date.now()) / 1000));
      el("review-clock").textContent = seconds ? "Review expires in " + seconds + "s" : "Review expired";
    };
    tick();
    clockTimer = setInterval(tick, 1000);
  }
  if (state.review) expiryTimer = setTimeout(async () => {
    if (pending) return;
    try {
      await sync();
      if (!state.review && !state.receipt) el("status").textContent = "Your review expired. Review the payment again to continue.";
    } catch (error) { el("status").textContent = error.message; }
  }, Math.ceil(state.review.expires_in_seconds * 1000) + 100);
  render();
  if (restore && state.review) el("payment").showModal();
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
  clearInterval(clockTimer);
  el("review-clock").hidden = true;
  el("status").textContent = error.message;
  el("title").textContent = "Reconnect to your payment";
  el("primary").disabled = false;
  el("primary").textContent = "Reload payment";
  el("configure").disabled = true;
  el("open-payment").disabled = false;
  el("open-label").textContent = "Reconnect to payment";
  el("primary").hidden = false;
  el("receipt-head").hidden = el("reset").hidden = true;
  el("cancel").hidden = el("challenge").hidden = el("verify").hidden = el("download").hidden = true;
}

function prepareReview() {
  return request("/api/review", { profile, scenario });
}

el("primary").onclick = () => {
  if (!state) return location.reload();
  if (!state.funding_plans[profile][scenario].payment_cents) return el("payment").close();
  if (!state.deposit) return run("Setting aside funds…", async () => {
    await request("/api/deposit", {});
    await prepareReview();
  });
  if (state.review) return run("Sending payment…", () => request("/api/approve", { approval: state.review.id }));
  return run("Preparing review…", prepareReview);
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
function previewSettings() {
  const choice = el("profile").querySelector(":checked").value;
  for (const input of el("profile").querySelectorAll("input")) {
    const estimate = state.funding_plans[input.value][el("scenario").value];
    input.labels[0].querySelector("small").textContent = money(estimate.cost_cents) + " plan fee";
  }
  const plan = state.funding_plans[choice][el("scenario").value];
  el("access-help").textContent = profiles[choice].terms;
  el("draft-bill").textContent = money(plan.bill_cents);
  el("draft-cost").textContent = money(plan.cost_cents);
}

el("explain-plan").onclick = event => {
  const button = event.currentTarget;
  button.setAttribute("aria-expanded", String(button.getAttribute("aria-expanded") !== "true"));
};
el("configure").onclick = () => {
  for (const input of el("profile").querySelectorAll("input")) input.checked = input.value === profile;
  el("scenario").value = scenario;
  previewSettings();
  el("settings").showModal();
};
el("scenario").onchange = el("profile").onchange = previewSettings;
el("apply").onclick = () => {
  profile = el("profile").querySelector(":checked").value;
  scenario = el("scenario").value;
  sessionStorage.setItem("funding-profile", profile);
  sessionStorage.setItem("fuel-scenario", scenario);
  render();
  el("settings").close();
};
el("open-payment").onclick = () => {
  el("payment").showModal();
  if (state?.deposit && !state.review && !state.receipt && state.funding_plans[profile][scenario].payment_cents) {
    run("Preparing review…", prepareReview);
  }
};
for (const button of document.querySelectorAll("[data-dismiss]")) {
  button.onclick = () => button.closest("dialog").close();
}
el("reset").onclick = () => run("Starting over…", async () => {
  await request("/api/reset", {});
  profile = "quick pay";
  scenario = "oil stress";
  sessionStorage.removeItem("funding-profile");
  sessionStorage.removeItem("fuel-scenario");
  el("payment").close();
});
el("proof").ontoggle = () => { el("details").textContent = el("proof").open && currentStep ? JSON.stringify(currentStep, null, 2) : ""; };
sync(true).catch(showRetry);
