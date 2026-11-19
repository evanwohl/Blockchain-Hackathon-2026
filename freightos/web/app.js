import { downloadReceipt, el, facts, money, request, tokenUnits } from "./common.js";

const profiles = {
  "quick pay": { source: "Return-load quick pay", comparison: "invoice quick pay", fallback: "Quick pay on $3,000 invoice", terms: "Quick pay releases your invoice early for a 5% fee." },
  "credit line": { source: "Bank credit line", comparison: "using bank credit alone", fallback: "Credit line alone", terms: "The sample credit line offers $800 at 1% for the term." },
  "cash on hand": { source: "Available cash", comparison: "paying from cash", fallback: "Paying from cash", terms: "Available cash covers the bill by its due date." }
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
  el("fuel-day").textContent = "Day " + bill.day;
  el("method").textContent = "FreightOS compares the funds and payment terms available to this load. It chooses the lowest estimated total fee among combinations that cover the bill by day " + bill.day + " and preserve your minimum cash balance.";
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

function fundingRibbon(startY, endY, thickness) {
  if (!thickness) return "";
  const boundary = offset => {
    const start = startY + offset;
    const end = endY + offset;
    return [start, start - 15, end + 30, end];
  };
  const [top, topBend, topFold, topEnd] = boundary(-thickness / 2);
  const [bottom, bottomBend, bottomFold, bottomEnd] = boundary(thickness / 2);
  return `M0 ${top} C100 ${topBend} 154 ${topFold} 280 ${topEnd}
    L280 ${bottomEnd} C154 ${bottomFold} 100 ${bottomBend} 0 ${bottom} Z`;
}

function explainChoice(plan) {
  const primary = plan.calendar.invoices.find(invoice => invoice.id === "primary load");
  if (plan.profile === "cash on hand") {
    return ["Pay from cash.", "Your available cash covers the fuel bill on time. You can leave your reserve untouched and collect the freight invoice on its normal schedule."];
  }
  if (plan.profile === "credit line") {
    return ["Borrow only the gap.", "Using your reserve cuts the bank draw to " + money(plan.other_funds_cents) + ". Financing the whole bill on credit would cost " + money(plan.alternative_cost_cents) + "."];
  }
  const backhaul = plan.calendar.invoices.find(invoice => invoice.id === "backhaul");
  const election = plan.invoice_elections.find(item => item.invoice === backhaul.id);
  const reserve = plan.funding.find(item => item.source === "reserve redemption");
  const dollars = cents => money(cents).replace(/\.00$/, "");
  return ["Advance the smaller invoice.", "Quick pay on the " + dollars(primary.face_cents) + " freight invoice costs " + dollars(plan.alternative_cost_cents) + ". The " + dollars(backhaul.face_cents) + " return load costs " + dollars(backhaul.face_cents - election.receipt_cents) + " to advance. Using your reserve adds " + dollars(reserve.cost_cents) + "."];
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
  const reserve = 54 * plan.payment_cents / plan.bill_cents;
  const other = 54 - reserve;
  el("reserve-source").hidden = !plan.payment_cents;
  el("reserve-ribbon").setAttribute("d", fundingRibbon(other ? 40 : 80, 53 + reserve / 2, reserve));
  el("other-ribbon").setAttribute("d", fundingRibbon(reserve ? 122 : 80, 107 - other / 2, other));
  const [heading, reason] = explainChoice(plan);
  el("plan-head").textContent = heading;
  el("edge-reason").textContent = reason;
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
    stage: 2, title: "Reserve payment delivered", button: "Start another demo",
    description: "Your fuel vendor received the reserve payment. The remaining amount below is yours to pay separately.",
    rows: [["Sent to the fuel vendor", tokenUnits(receipt.vendor_received_usdc_raw) + " USDC"], ["Still to pay separately", money(plan.other_funds_cents)]]
  };
  if (!plan.payment_cents) return {
    stage: -1, title: "Pay from available cash", button: "Done",
    description: "Pay the vendor directly from your available cash.",
    rows: [["Cash needed", money(plan.bill_cents)], ["Financing cost", money(plan.cost_cents)]]
  };
  if (review) return {
    stage: 1, title: "Review your payment", button: "Send " + money(plan.payment_cents) + " to fuel vendor",
    description: "Send demo dollars (USDC) from your fuel reserve to this vendor. Check the recipient and amount before sending.",
    rows: [["To", "Fuel vendor · " + review.payee_account.slice(0, 4) + "…" + review.payee_account.slice(-4)], ["Vendor receives", tokenUnits(review.usdc_raw) + " USDC"], ["Still to cover separately", money(plan.other_funds_cents)], ["Network fee", review.network_fee_lamports / 1e9 + " SOL"]]
  };
  if (state.deposit) return {
    stage: 1, title: "Your reserve is ready", button: "Review " + money(plan.payment_cents) + " payment",
    description: "Confirm who will receive the funds and how much to send.",
    rows: [["From your reserve", money(plan.payment_cents)], ["Still to pay separately", money(plan.other_funds_cents)]]
  };
  const amount = tokenUnits(state.wallet_usdc_raw);
  return {
    stage: 0, title: "Set up your fuel reserve", button: "Move $" + amount + " into reserve",
    description: "Your fuel reserve holds digital dollars (USDC) for bills. Moving your demo balance there sets it aside. You approve the vendor payment in the next step.",
    rows: [["Move to your reserve", "$" + amount], ["For this fuel bill", money(plan.payment_cents)]]
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
  el("configure").disabled = busy || Boolean(review || receipt);
  el("open-payment").disabled = busy;
  el("open-label").textContent = receipt ? "View receipt" : review ? "Return to review" : plan.payment_cents ? (state.deposit ? "Review " : "Prepare ") + money(plan.payment_cents) + " payment" : "View cash plan";
  el("fund-caption").textContent = receipt ? "Reserve payment delivered" : review ? "Ready for your approval" : plan.cost_cents ? money(plan.cost_cents) + " in estimated fees" : "No financing fees";
  el("fee-saving").hidden = Boolean(receipt || review || !plan.savings_cents);
  el("fee-saving").textContent = "Save " + money(plan.savings_cents) + " compared with " + profiles[profile].comparison + ".";
  const separate = profile === "credit line" ? "your bank credit line" : "return-load quick pay";
  el("payment-scope").textContent = !plan.payment_cents ? "Pay the fuel vendor directly from your available cash."
    : (receipt ? "Your vendor received " : "After approval, your vendor receives ") + money(plan.payment_cents)
      + " in demo dollars (USDC). Pay the remaining " + money(plan.other_funds_cents) + " separately using " + separate + ".";
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
  const previousApproval = state?.review?.id;
  state = await request("/api/state");
  if (previousApproval && state.receipt?.approval === previousApproval) el("status").textContent = "";
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
    } catch { showRetry(); }
  }, Math.ceil(state.review.expires_in_seconds * 1000) + 100);
  render();
  delete el("desk").dataset.loading;
  if (restore && (state.review || reopenPayment) && !el("payment").open) el("payment").showModal();
  reopenPayment = false;
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
    try { await sync(); } catch { showRetry(); }
    pending = "";
    render();
  }
}

function showRetry() {
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
