import { downloadReceipt, el, facts, money, request, tokenUnits } from "./common.js";

const profiles = {
  "quick pay": { source: "Early pay for your return load", comparison: "invoice quick pay", fallback: "If you get the main invoice paid early", terms: "In this example, getting paid in one day costs 5% of the invoice." },
  "credit line": { source: "Bank credit line", comparison: "using bank credit alone", fallback: "If you borrow the whole amount", terms: "The sample credit line offers $800 at 1% for the term." },
  "cash on hand": { source: "Available cash", comparison: "paying from cash", fallback: "If you pay directly with cash", terms: "Available cash covers the bill by its due date." }
};
let state;
let pending = "";
let expiryTimer;
let clockTimer;
let currentStep;
let displayedPlan;
let reopenPayment = false;

let profile = sessionStorage.getItem("funding-profile") || "quick pay";
let scenario = sessionStorage.getItem("fuel-scenario") || "oil stress";

function renderCalendar(plan) {
  const bill = plan.calendar.bills[0];
  const primary = plan.invoice_elections.find(item => item.invoice === "primary load");
  el("load-id").textContent = plan.load;
  el("bill-due").textContent = "Day " + bill.day;
  el("method").textContent = "FreightOS checks which money will arrive by day " + bill.day + ". It chooses the lowest-cost way to cover the fuel bill while keeping the cash you need on hand.";
  el("invoice-day").textContent = "Day " + primary.day;
  const gap = primary.day - bill.day;
  el("timing").textContent = gap === 0 ? "The fuel bill and freight payment fall on the same day."
    : "Fuel is due " + Math.abs(gap) + (Math.abs(gap) === 1 ? " day " : " days ") + (gap > 0 ? "before" : "after") + " the freight invoice pays.";
  const events = [
    { day: bill.day, label: "Fuel bill due", note: "Vendor payment deadline", amount: bill.cents, bill: true },
    ...plan.invoice_elections.map(item => ({
      day: item.day, label: item.invoice === "backhaul" ? "Return-load invoice" : "Freight invoice",
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

function explainChoice(plan) {
  const primary = plan.calendar.invoices.find(invoice => invoice.id === "primary load");
  if (plan.profile === "cash on hand") {
    return ["Use the cash you have", "You have enough to pay for fuel on time. Collect both invoices on their usual dates."];
  }
  if (plan.profile === "credit line") {
    return ["Borrow only what’s missing", "Use the money set aside for fuel, then borrow " + money(plan.other_funds_cents) + " from your bank. Borrowing the whole bill would cost " + money(plan.alternative_cost_cents) + "."];
  }
  const backhaul = plan.calendar.invoices.find(invoice => invoice.id === "backhaul");
  const election = plan.invoice_elections.find(item => item.invoice === backhaul.id);
  const dollars = cents => money(cents).replace(/\.00$/, "");
  return ["Get paid early on the return load", "Ask for early pay on the " + dollars(backhaul.face_cents)
    + " return load. You get " + dollars(election.receipt_cents) + " after the fee. You can collect the full "
    + dollars(primary.face_cents) + " for the main load on day " + plan.invoice_elections.find(item => item.invoice === primary.id).day + "."];
}

function renderPlan(plan) {
  const profile = profiles[plan.profile];
  renderCalendar(plan);
  el("saved").textContent = money(plan.savings_cents);
  el("plan-cost").textContent = money(plan.cost_cents);
  el("alternative").textContent = profile.fallback;
  el("other-cost").textContent = money(plan.alternative_cost_cents);
  el("bill").textContent = el("for-fuel").textContent = money(plan.bill_cents);
  const [heading, reason] = explainChoice(plan);
  el("plan-head").textContent = heading;
  el("edge-reason").textContent = reason;
  const reserve = plan.funding.find(item => item.source === "reserve redemption");
  const sources = [];
  if (reserve) sources.push(["Money set aside for fuel", reserve.net_proceeds_cents, reserve.cost_cents]);
  sources.push([profile.source, plan.other_funds_cents, plan.cost_cents - (reserve?.cost_cents || 0)]);
  el("sources").replaceChildren(...sources.map(([name, amount, cost]) => {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = name;
    row.append(label);
    row.insertCell().textContent = money(amount);
    row.insertCell().textContent = money(cost);
    return row;
  }));
  el("terms").textContent = profile.terms + (reserve ? " The cost of using your fuel money includes a fixed fee and the interest you give up." : "");
  const g = plan.gemini;
  el("market-assumption").textContent = plan.scenario === "oil stress"
    ? g.gallons + " gallons · Higher price estimate"
    : g.gallons + " gallons · Original estimate";
  const [low, high] = g.extra_fuel_cents_interval;
  const budget = state.funding_plans[plan.profile]["fuel budget"].bill_cents;
  const selected = plan.scenario === "oil stress" ? "This plan includes the higher estimate." : "This plan uses the original estimate.";
  el("signal").textContent = "The original estimate for " + g.gallons + " gallons is " + money(budget)
    + ". At the $" + g.stress_level + " oil price in the recorded Gemini contract, this model adds "
    + money(low) + " to " + money(high) + ". " + selected;
}

function paymentView(plan) {
  const { receipt, review } = state;
  if (receipt) return {
    stage: 2, title: "Your fuel payment arrived", button: "Start another demo",
    description: "Your fuel vendor received the reserve payment in demo dollars.",
    rows: [["Received by fuel company", money(receipt.vendor_received_usdc_raw / 10000)], [state.manual_payment ? "Recorded separately by you" : "Still to pay separately", money(plan.other_funds_cents)]]
  };
  if (!plan.payment_cents) return {
    stage: -1, title: "Pay from available cash", button: "Done",
    description: "Pay the vendor directly from your available cash.",
    rows: [["Cash needed", money(plan.bill_cents)], ["Financing cost", money(plan.cost_cents)]]
  };
  if (review) return {
    stage: 1, title: "Review your payment", button: "Send " + money(plan.payment_cents) + " to fuel company",
    description: "Check who gets the money and how much you still need to pay yourself.",
    rows: [["To", "Fuel company"], ["Vendor receives", money(review.usdc_raw / 10000)], ["Still to cover separately", money(plan.other_funds_cents)]]
  };
  if (state.deposit) return {
    stage: 1, title: "Your fuel money is ready", button: "Review " + money(plan.payment_cents) + " payment",
    description: "Confirm who will receive the funds and how much to send.",
    rows: [["From your reserve", money(plan.payment_cents)], ["Still to pay separately", money(plan.other_funds_cents)]]
  };
  const amount = tokenUnits(state.wallet_usdc_raw);
  return {
    stage: 0, title: "Set money aside for fuel", button: "Set aside $" + amount,
    description: "This moves your demo dollars into a fuel reserve for bills. The fuel company receives money only after you approve the next step.",
    rows: [["Set aside for fuel", "$" + amount], ["Planned payment to fuel company", money(plan.payment_cents)]]
  };
}

function render() {
  if (!state) return;
  const plan = state.funding_plans[profile][scenario];
  if (displayedPlan !== plan) {
    renderPlan(plan);
    displayedPlan = plan;
  }
  const { review, receipt } = state;
  const view = paymentView(plan);
  const busy = Boolean(pending);
  const entry = state.manual_payment;
  const separate = profiles[profile].source.toLowerCase();
  const delivered = receipt ? receipt.vendor_received_usdc_raw / 10000 : 0;
  const balance = plan.bill_cents - delivered - (entry?.cents || 0);
  const recordable = receipt || !plan.payment_cents;
  el("balance").textContent = money(balance);
  el("original").hidden = balance === plan.bill_cents;
  el("bill-status").textContent = entry ? "Paid" : receipt ? "Partly paid" : "Unpaid";
  el("bill-status").dataset.stage = entry ? "paid" : receipt ? "partial" : "open";
  el("configure").disabled = busy || Boolean(review || receipt || entry);
  el("open-payment").disabled = el("save-record").disabled = busy;
  el("reserve-receipt").hidden = !receipt;
  el("new-demo").hidden = !entry;
  el("reserve-receipt").disabled = el("new-demo").disabled = busy;
  el("return-bill").hidden = !receipt;
  el("action-title").textContent = entry ? "This bill is paid" : recordable ? "Pay " + money(balance) + " directly" : review ? "Check your payment" : "Pay " + money(plan.payment_cents) + " through FreightOS";
  el("open-label").textContent = entry ? "View your payment entry" : recordable ? "I already paid this" : review ? "Return to approval" : state.deposit ? "Review payment" : "Set aside money for fuel";
  el("payment-scope").textContent = entry ? "Your payment record covers the full bill."
    : recordable ? "Use " + separate + " to pay the fuel company yourself. Once you’ve paid, enter it here."
    : state.deposit ? "Check the recipient and amount before approving the reserve payment."
    : "Set aside the full $" + tokenUnits(state.wallet_usdc_raw) + " demo balance for fuel. On the next screen, you can approve the amount going to the fuel company.";
  el("plan-jump").textContent = "How we estimated the " + money(plan.cost_cents) + " cost ↓";
  const rows = [];
  if (plan.payment_cents) rows.push([receipt ? "Received by fuel company" : "FreightOS pays after you approve", money(receipt ? delivered : plan.payment_cents)]);
  const outsideLabel = { "quick pay": "You pay from your return load", "credit line": "You pay using bank credit", "cash on hand": "You pay with cash" };
  rows.push([entry ? "Recorded by you" : outsideLabel[profile], money(plan.other_funds_cents)]);
  facts(el("payment-log"), rows);
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
  el("network-details").hidden = !review;
  el("network-details").textContent = review ? "Recipient account " + review.payee_account + ". Network fee " + review.network_fee_lamports / 1e9 + " SOL (demo funds)." : "";
  el("proof").hidden = !plan.payment_cents;
  el("details").hidden = !currentStep;
  if (el("proof").open && currentStep) el("details").textContent = JSON.stringify(currentStep, null, 2);
}

async function sync(restore = false) {
  const previousApproval = state?.review?.id;
  state = await request("/api/state");
  if (previousApproval && state.receipt?.approval === previousApproval) el("status").textContent = "";
  const locked = (state.receipt || state.review)?.decision || state.manual_payment;
  if (locked) {
    scenario = locked.scenario;
    profile = locked.profile;
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
    } catch { showRetry(); }
  }, Math.ceil(state.review.expires_in_seconds * 1000) + 100);
  render();
  delete el("desk").dataset.loading;
  if (restore && (state.review || reopenPayment) && !el("payment").open) el("payment").showModal();
  reopenPayment = false;
}

async function run(label, action, feedback = el("status")) {
  if (pending) return;
  pending = label;
  feedback.textContent = "";
  render();
  try {
    await action();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    try { await sync(); } catch { showRetry(); }
    pending = "";
    render();
  }
}

function showRetry() {
  el("record").close();
  clearTimeout(expiryTimer);
  clearInterval(clockTimer);
  if (el("payment").open) {
    reopenPayment = true;
    el("payment").close();
  }
  state = null;
  el("desk").dataset.loading = "";
  el("load-message").textContent = "We couldn’t confirm the current payment status. Check again to see your latest plan or receipt.";
  el("reconnect").hidden = false;
  el("reconnect").focus();
}

el("reconnect").onclick = async () => {
  const button = el("reconnect");
  button.disabled = true;
  el("load-message").textContent = "Checking your payment status…";
  try {
    await sync(true);
    el("status").textContent = "";
    button.hidden = true;
    if (!el("payment").open) el("open-payment").focus();
  } catch {
    showRetry();
  } finally {
    button.disabled = false;
  }
};

function prepareReview() {
  return request("/api/review", { profile, scenario });
}

el("primary").onclick = () => {
  if (!state) return;
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
  el("verified").textContent = "Payment checked. The fuel company received " + money(proof.vendor_received_usdc_raw / 10000) + " in demo dollars.";
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
let recordRequest;

function openRecord(plan) {
  const undo = Boolean(state.manual_payment);
  recordRequest = { undo, body: { version: state.record_version, profile, scenario } };
  el("record-title").textContent = undo ? "Recorded outside payment" : "Record your payment";
  el("record-help").textContent = undo ? "You marked this amount as paid outside FreightOS. Undoing the entry reopens that balance."
    : "Confirm you already paid the fuel vendor outside FreightOS. This updates the bill record only.";
  facts(el("record-facts"), [["Load", plan.load], ["Source", profiles[profile].source], ["Amount", money(plan.other_funds_cents)]]);
  el("save-record").textContent = undo ? "Undo payment entry" : "I paid " + money(plan.other_funds_cents);
  el("record-error").textContent = "";
  el("record").showModal();
}

el("save-record").onclick = async () => {
  const { undo, body } = recordRequest;
  await run("Updating bill…", () => request(undo ? "/api/undo-record" : "/api/record-payment", body), el("record-error"));
  if (state && (undo ? !state.manual_payment : state.manual_payment?.profile === body.profile && state.manual_payment?.scenario === body.scenario)) {
    el("record").close();
    el("open-payment").focus();
  }
};
el("return-bill").onclick = () => { el("payment").close(); el("open-payment").focus(); };
el("reserve-receipt").onclick = () => el("payment").showModal();
el("open-payment").onclick = () => {
  const plan = state.funding_plans[profile][scenario];
  if (state.receipt || !plan.payment_cents) return openRecord(plan);
  el("payment").showModal();
  if (state?.deposit && !state.review && !state.receipt && state.funding_plans[profile][scenario].payment_cents) {
    run("Preparing review…", prepareReview);
  }
};
for (const button of document.querySelectorAll("[data-dismiss]")) {
  button.onclick = () => button.closest("dialog").close();
}
el("new-demo").onclick = el("reset").onclick = () => run("Starting over…", async () => {
  await request("/api/reset", { version: state.record_version });
  profile = "quick pay";
  scenario = "oil stress";
  sessionStorage.removeItem("funding-profile");
  sessionStorage.removeItem("fuel-scenario");
  el("payment").close();
});
el("proof").ontoggle = () => { el("details").textContent = el("proof").open && currentStep ? JSON.stringify(currentStep, null, 2) : ""; };
sync(true).catch(showRetry);
