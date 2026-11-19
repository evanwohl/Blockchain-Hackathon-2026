import { cashPriority, downloadReceipt, el, facts, money, oilOptions, request, saveFile, tokenUnits, usesCredit } from "./common.js";
import { bindWallet, walletState } from "./wallet.js";

const roundedDollars = cents => money(cents).replace(/\.00$/, "");

const profiles = {
  "quick pay": { source: "Early pay for your return load", comparison: "invoice quick pay", fallback: "If you get the main invoice paid early", terms: "In this example, getting paid in one day costs 5% of the invoice." },
  "credit line": { source: "Bank credit line", comparison: "using bank credit alone", fallback: "If you borrow the whole amount", terms: "The sample credit line offers $800 at 1% for the term." },
  "cash on hand": { source: "Available cash", comparison: "paying from cash", fallback: "If you pay directly with cash", terms: "Available cash covers the bill by its due date." }
};
profiles["road cash"] = profiles["credit line"];
let state;
let pending = "";
let expiryTimer;
let clockTimer;
let currentStep;
let displayedPlan;
let reopenPayment = false;
el("schedule").open = window.matchMedia("(min-width: 761px)").matches;

let profile = sessionStorage.getItem("funding-profile") || "quick pay";
let scenario = sessionStorage.getItem("fuel-scenario") || "oil stress";

const tripStart = el("trip-start");
tripStart.value = sessionStorage.getItem("trip-start") || "";
const shortDate = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

function tripDate(day) {
  const date = tripStart.valueAsDate;
  if (!date || !tripStart.validity.valid) return null;
  date.setUTCDate(date.getUTCDate() + day - 1);
  return date;
}

function dayLabel(day) {
  const date = tripDate(day);
  return date ? shortDate.format(date) : "Day " + day;
}

function scheduleEvents(plan) {
  const bill = plan.calendar.bills[0];
  return [
    { day: bill.day, label: "Fuel bill due", note: "Vendor payment deadline", amount: bill.cents, bill: true, outgoing: true },
    ...plan.invoice_elections.map(item => ({
      day: item.day, label: item.invoice === "backhaul" ? "Return-load invoice" : "Freight invoice",
      note: item.option === "standard" ? "Scheduled payment" : "Early payment", amount: item.receipt_cents
    })),
    ...plan.funding.flatMap(item => {
      const terms = plan.calendar.funding.find(source => source.id === item.source);
      const reserve = item.source === "reserve redemption";
      return [
        { day: terms.available_day, label: reserve ? "Reserve available" : "Bank credit available",
          note: "Planned funding", amount: item.net_proceeds_cents },
        { day: terms.restore_day, label: reserve ? "Restore fuel reserve" : "Repay bank credit",
          note: reserve ? "Planned principal replenishment" : "Principal and estimated fee",
          amount: item.net_proceeds_cents + (reserve ? 0 : item.cost_cents), outgoing: true }
      ];
    })
  ].sort((a, b) => a.day - b.day);
}

function renderCalendar(plan) {
  const bill = plan.calendar.bills[0];
  const primary = plan.invoice_elections.find(item => item.invoice === "primary load");
  el("load-id").textContent = plan.load;
  el("bill-due").textContent = dayLabel(bill.day);
  el("method").textContent = plan.profile === "road cash"
    ? "We find the smallest fuel reserve that covers every tested bill within " + money(plan.cash_policy.fee_allowance_cents) + " of its lowest fee. Then we price the bank draw for this bill."
    : "FreightOS compares funding that arrives by day " + bill.day + " and prices the fees through repayment.";
  el("invoice-day").textContent = dayLabel(primary.day);
  const gap = primary.day - bill.day;
  el("day-gap").textContent = gap ? Math.abs(gap) + "-day gap" : "Same-day schedule";
  el("timing").textContent = gap === 0 ? "The fuel bill and freight payment fall on the same day."
    : "Fuel is due " + Math.abs(gap) + (Math.abs(gap) === 1 ? " day " : " days ") + (gap > 0 ? "before" : "after") + " the freight invoice pays.";
  el("timeline").replaceChildren(...scheduleEvents(plan).map(event => {
    const row = document.createElement("li");
    const day = document.createElement("span");
    const pin = document.createElement("i");
    const label = document.createElement("span");
    const note = document.createElement("small");
    const amount = document.createElement("strong");
    day.className = "day";
    pin.className = "pin";
    label.className = "event-label";
    day.textContent = dayLabel(event.day);
    label.textContent = event.label;
    note.textContent = event.note;
    amount.textContent = (event.outgoing ? "−" : "+") + money(event.amount);
    if (event.bill) row.dataset.bill = "";
    label.append(note);
    row.append(day, pin, label, amount);
    return row;
  }));
}

tripStart.onchange = () => {
  sessionStorage.setItem("trip-start", tripStart.validity.valid ? tripStart.value : "");
  if (displayedPlan) renderCalendar(displayedPlan);
};

el("trip-form").onsubmit = event => {
  event.preventDefault();
  const stamp = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, "");
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FreightOS//Load schedule//EN", "CALSCALE:GREGORIAN"];
  for (const item of scheduleEvents(displayedPlan)) {
    const date = tripDate(item.day).toISOString().slice(0, 10).replaceAll("-", "");
    const key = item.label.toLowerCase().replaceAll(" ", "-");
    lines.push("BEGIN:VEVENT", "UID:" + displayedPlan.load + "-" + key + "@freightos.local",
      "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + date, "SUMMARY:" + displayedPlan.load + " - " + item.label,
      "DESCRIPTION:Planned demo amount " + money(item.amount).replaceAll(",", "\\,") + ".", "END:VEVENT");
  }
  lines.push("END:VCALENDAR", "");
  saveFile(displayedPlan.load + "-schedule.ics", lines.join("\r\n"), "text/calendar;charset=utf-8");
};

function explainChoice(plan) {
  const primary = plan.calendar.invoices.find(invoice => invoice.id === "primary load");
  if (plan.profile === "cash on hand") {
    return ["Use the cash you have", "You have enough to pay for fuel on time. Collect both invoices on their usual dates."];
  }
  if (plan.profile === "road cash") {
    const bank = plan.funding.find(s => s.source === "approved bank line");
    const terms = plan.calendar.funding.find(s => s.id === bank.source);
    return ["Keep cash for the road", "Use " + money(plan.payment_cents) + " from the fuel reserve and borrow "
      + money(bank.net_proceeds_cents) + ". Plan to repay the bank " + money(bank.net_proceeds_cents + bank.cost_cents)
      + " on day " + terms.restore_day + ", when the freight invoice pays."];
  }
  if (usesCredit(plan.profile)) {
    return ["Borrow only what’s missing", "Use the money set aside for fuel, then borrow " + money(plan.other_funds_cents) + " from your bank. Borrowing the whole bill would cost " + money(plan.alternative_cost_cents) + "."];
  }
  const backhaul = plan.calendar.invoices.find(invoice => invoice.id === "backhaul");
  const election = plan.invoice_elections.find(item => item.invoice === backhaul.id);
  return ["Get paid early on the return load", "Ask for early pay on the " + roundedDollars(backhaul.face_cents)
    + " return load. You get " + roundedDollars(election.receipt_cents) + " after the fee. You can collect the full "
    + roundedDollars(primary.face_cents) + " for the main load on day " + plan.invoice_elections.find(item => item.invoice === primary.id).day + "."];
}

function drawTicket(id, { x, y, depth, amount, source, load }) {
  const ticket = el(id);
  ticket.style.display = amount ? "" : "none";
  if (!amount) return;
  ticket.setAttribute("transform", `translate(${x} ${y})`);
  ticket.querySelector(".face").setAttribute("transform", "matrix(.92 -.22 .66 .36 0 0)");
  const right = 354 * .92, rise = -354 * .22;
  const offset = 145 * .66, drop = 145 * .36;
  ticket.querySelector(".slip-side").setAttribute("d",
    `M0 0l${offset} ${drop}v${depth}l${-offset} ${-drop}Z`);
  ticket.querySelector(".slip-front").setAttribute("d",
    `M${offset} ${drop}l${right} ${rise}v${depth}l${-right} ${-rise}Z`);
  const edges = [];
  for (let z = 1; z < depth; z += 2.2) {
    edges.push(`M0 ${z}l${offset} ${drop}l${right} ${rise}`);
  }
  ticket.querySelector(".slip-edges").setAttribute("d", edges.join(" "));
  ticket.querySelector(".ticket-value").textContent = roundedDollars(amount);
  ticket.querySelector(".ticket-source").textContent = source;
  ticket.querySelector(".ticket-load").textContent = load;
}

function drawFunding(plan) {
  const reserveDepth = 48 * plan.payment_cents / plan.bill_cents;
  drawTicket("direct-ticket", { x: 38, y: plan.payment_cents ? 175 : 127, depth: 48 - reserveDepth,
    amount: plan.other_funds_cents, source: profiles[plan.profile].source, load: plan.load });
  drawTicket("reserve-ticket", { x: 13, y: 93, depth: reserveDepth, amount: plan.payment_cents,
    source: "Fuel reserve", load: plan.load });
}

function renderSource(id, label, amount, explanation) {
  const row = el(id);
  row.hidden = !amount;
  row.querySelector("summary span").textContent = label;
  row.querySelector("summary strong").textContent = money(amount);
  row.querySelector("p").textContent = explanation;
}

function renderPlan(plan) {
  const profile = profiles[plan.profile];
  renderCalendar(plan);
  drawFunding(plan);
  el("allocation").style.setProperty("--reserve-share", (100 * plan.payment_cents / plan.bill_cents) + "%");
  el("saved").textContent = money(plan.savings_cents);
  el("plan-cost").textContent = el("chosen-cost").textContent = money(plan.cost_cents);
  const scale = Math.max(1, plan.cost_cents, plan.alternative_cost_cents);
  for (const [id, cost] of [["chosen-cost", plan.cost_cents], ["other-cost", plan.alternative_cost_cents]]) {
    el(id).parentElement.style.setProperty("--share", (100 * cost / scale) + "%");
  }
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
  renderFuelPolicy(plan);
  const g = plan.gemini;
  el("market-assumption").textContent = plan.scenario === "oil stress"
    ? g.gallons + " gallons · Higher price estimate"
    : g.gallons + (plan.scenario === "fuel budget" ? " gallons · Original estimate" : " gallons · $" + g.stress_level + " oil");
  const [low, high] = g.extra_fuel_cents_interval;
  const budget = state.funding_plans[plan.profile]["fuel budget"].bill_cents;
  const selected = plan.scenario !== "fuel budget" ? "This plan includes the higher estimate." : "This plan uses the original estimate.";
  el("signal").textContent = "The original estimate for " + g.gallons + " gallons is " + money(budget)
    + ". At the $" + g.stress_level + " oil price in the recorded Gemini contract, this model adds "
    + money(low) + " to " + money(high) + ". " + selected;
}

function renderFuelPolicy(plan) {
  const edge = state.pairing_values[plan.profile][plan.scenario];
  const options = usesCredit(plan.profile) ? "Bank credit and return-invoice options" : "Return-invoice options";
  facts(el("pair-fees"), [
    ["Main invoice and existing cash", money(edge.base_cents)],
    ["Add only the fuel reserve", money(edge.reserve_only_cents)],
    ["Add only outside funding", money(edge.outside_only_cents)],
    ["Allow both", money(edge.joint_cents)]
  ]);
  const limit = edge.boundary;
  el("pair-limit").textContent = limit
    ? options + " can work with the reserve to save " + money(edge.pairing_savings_cents) + " versus the cheaper group alone. With "
      + money(limit.minimum_useful_capacity_cents) + " available in the reserve, fees are " + money(limit.cost_at_threshold_cents)
      + ". One cent less brings fees to " + money(limit.cost_one_cent_below_cents) + "."
    : "Your available cash already covers the bill. Combining sources adds no fee savings.";
  const g = plan.gemini;
  const market = state.market_levels[g.stress_level];
  el("quote-evidence").textContent = "Recorded " + market.expiry.slice(0, 10) + " expiry. "
    + (market.recorded_buy_yes == null ? "The snapshot has no YES ask at this level." : "The recorded YES offer was $" + market.recorded_buy_yes + " per contract.")
    + " Each level is a planning scenario. Your local diesel price can move differently.";
  const policy = state.invoice_policies[plan.profile];
  el("policy-detail").hidden = !policy;
  if (!policy) {
    el("policy-copy").textContent = "This option assumes you already have enough cash for the selected bill.";
    return;
  }
  const base = state.funding_plans[plan.profile];
  const budgets = Object.values(base).map(choice => choice.bill_cents);
  el("policy-copy").textContent = "Fuel bills from " + roundedDollars(Math.min(...budgets)) + " to " + roundedDollars(Math.max(...budgets))
    + " can use the same invoice terms. Keep up to " + roundedDollars(policy.peak_reserve_cents) + " available in the reserve.";
  const plans = base;
  const sorted = Object.entries(policy.outcomes).sort(([a], [b]) => plans[a].bill_cents - plans[b].bill_cents);
  el("policy-rows").replaceChildren(...sorted.map(([key, outcome]) => {
    const row = document.createElement("tr");
    const header = document.createElement("th");
    header.scope = "row";
    header.textContent = money(plans[key].bill_cents);
    row.append(header);
    row.insertCell().textContent = money(outcome.reserve_cents);
    row.insertCell().textContent = money(outcome.cost_cents);
    if (key === plan.scenario) row.className = "current-budget";
    return row;
  }));
  el("policy-method").textContent = plan.cash_policy
    ? "Both invoices stay on their usual terms. Across these fuel budgets, the most you pay above the lowest fee with the full reserve available is "
      + money(policy.worst_regret_cents) + "."
    : "We hold both invoice choices fixed, then resize funding for each fuel budget. The most this costs above choosing invoices after seeing the bill is "
      + money(policy.worst_regret_cents) + " in these scenarios.";
}

function paymentView(plan) {
  const { receipt, review } = state;
  if (receipt) return {
    stage: 2, title: "Your fuel payment arrived", button: "Start another demo",
    description: state.manual_payment ? "Both parts of this bill are accounted for." : "The reserve payment is complete. Pay the remaining balance directly.",
    rows: [["Received by fuel company", money(receipt.vendor_received_usdc_raw / 10000)], [state.manual_payment ? "Recorded separately by you" : "Still to pay separately", money(plan.other_funds_cents)]]
  };
  if (!plan.payment_cents) return {
    stage: -1, title: "Pay from available cash", button: "Done",
    description: "Pay the vendor directly from your available cash.",
    rows: [["Cash needed", money(plan.bill_cents)], ["Financing cost", money(plan.cost_cents)]]
  };
  if (review) return {
    stage: 1, title: "Review your payment", button: "Send " + money(plan.payment_cents) + " to fuel company",
    description: "Approve this transfer from your fuel reserve.",
    rows: [["To the fuel company", money(review.usdc_raw / 10000)], ["Still to cover separately", money(plan.other_funds_cents)]]
  };
  if (state.deposit && !state.setup_cents[profile]) return {
    stage: 1, title: "Your fuel money is ready", button: "Review " + money(plan.payment_cents) + " payment",
    description: "Your reserve is funded. Review the transfer before sending.",
    rows: [["For the fuel company", money(plan.payment_cents)], ["Still to pay separately", money(plan.other_funds_cents)]]
  };
  const amount = roundedDollars(state.setup_cents[profile]);
  const left = money(state.wallet_usdc_raw / 10000 - state.setup_cents[profile]);
  return {
    stage: 0, title: "Set money aside for fuel", button: "Set aside " + amount,
    description: "This funds the reserve across the tested fuel budgets. Only the amount you approve next goes to the fuel company.",
    rows: [["Add to your fuel reserve", amount], ["Stays in your wallet", left], ["Planned payment to fuel company", money(plan.payment_cents)]]
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
  const unavailable = state.setup_cents[profile] === null && !review && !receipt && !state.manual_payment;
  walletState(state, profile, busy);
  const entry = state.manual_payment;
  const separate = profiles[profile].source.toLowerCase();
  el("reserve-ticket").classList.toggle("settled", Boolean(receipt));
  el("direct-ticket").classList.toggle("settled", Boolean(entry));
  const delivered = receipt ? receipt.vendor_received_usdc_raw / 10000 : 0;
  const balance = plan.bill_cents - delivered - (entry?.cents || 0);
  const recordable = receipt || !plan.payment_cents;
  const [whole, cents] = money(balance).split(".");
  const fraction = document.createElement("span");
  fraction.className = "balance-cents";
  fraction.textContent = "." + cents;
  el("balance").replaceChildren(whole, fraction);
  el("original").hidden = balance === plan.bill_cents;
  el("bill-status").textContent = entry ? "Paid" : receipt ? "Partly paid" : "Unpaid";
  el("bill-status").dataset.stage = entry ? "paid" : receipt ? "partial" : "open";
  el("configure").disabled = busy || Boolean(review || receipt || entry);
  el("open-payment").disabled = el("save-record").disabled = busy;
  el("reserve-receipt").hidden = !receipt;
  el("new-demo").hidden = !entry;
  el("reserve-receipt").disabled = el("new-demo").disabled = busy;
  el("return-bill").hidden = !receipt;
  el("action-title").textContent = entry ? "This bill is paid" : recordable ? "Pay " + roundedDollars(balance) + " directly"
    : review ? "Check your payment" : state.setup_cents[profile] ? "Cover fuel in two payments" : "Review the fuel payment";
  el("open-label").textContent = entry ? "View your payment entry" : recordable ? "I already paid this" : review ? "Return to approval" : state.deposit && !state.setup_cents[profile] ? "Review payment" : "Set aside " + money(state.setup_cents[profile]);
  el("payment-scope").textContent = entry ? "Your payment record covers the full bill."
    : recordable ? "Use " + separate + " to pay the fuel company yourself. Once you’ve paid, enter it here."
    : state.deposit && !state.setup_cents[profile] ? "Check the recipient and amount before approving the reserve payment."
    : "Your fuel reserve will cover " + roundedDollars(plan.payment_cents) + " after approval. Pay the other "
      + roundedDollars(plan.other_funds_cents) + " with " + separate + ".";
  el("plan-jump").textContent = plan.savings_cents ? "See how this saves " + money(plan.savings_cents) : "See why this plan costs " + money(plan.cost_cents);
  if (unavailable) {
    el("open-payment").disabled = true;
    el("open-label").textContent = "Reserve unavailable";
    el("action-title").textContent = "Choose another funding option";
    el("payment-scope").textContent = "The reserve cannot cover this plan right now. Open Payment options to compare another source.";
  }
  renderSource("reserve-part", receipt ? "Received by fuel company" : "Through FreightOS",
    receipt ? delivered : plan.payment_cents, receipt ? "This amount reached the fuel company in demo dollars. Open the receipt to check the payment."
      : "This is the part FreightOS sends from your fuel reserve when you approve the payment.");
  renderSource("direct-part", entry ? "Recorded by you" : "You pay separately", plan.other_funds_cents,
    entry ? "You marked this amount as paid outside FreightOS." : "Use " + separate + " to cover this part directly with the fuel company.");
  el("receipt-head").hidden = !receipt;
  el("receipt-load").textContent = plan.load;
  el("payment").classList.toggle("received", Boolean(receipt));
  el("return-bill").textContent = balance > 0 ? "Back to bill · " + roundedDollars(balance) + " left" : "Back to paid bill";
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
  oilOptions(el("scenario"), state.plans);
  if (previousApproval && state.receipt?.approval === previousApproval) el("status").textContent = "";
  const locked = (state.receipt || state.review)?.decision || state.manual_payment;
  const choice = new URLSearchParams(location.hash.slice(1));
  if (choice.has("plan")) {
    const requested = choice.get("plan");
    const fuel = choice.get("fuel");
    if (!locked && Object.hasOwn(state.funding_plans, requested) && Object.hasOwn(state.funding_plans[requested], fuel)) {
      profile = requested;
      scenario = fuel;
      sessionStorage.setItem("funding-profile", profile);
      sessionStorage.setItem("fuel-scenario", scenario);
    }
    history.replaceState(null, "", location.pathname);
  }
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
  el("wallet-dialog").close();
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
  if (state.setup_cents[profile] && !state.review) return run("Setting aside funds…", async () => {
    await request("/api/deposit", { profile, version: state.record_version, max_cents: state.setup_cents[profile] });
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
  el("verified").textContent = "Payment checked. The fuel company received " + money(proof.vendor_received_usdc_raw / 10000)
    + ". Your demo wallet held " + money(proof.wallet_usdc_raw / 10000) + " after the transfer.";
});
el("check-public").onclick = async () => {
  const button = el("check-public");
  button.disabled = true;
  el("public-account").hidden = true;
  el("public-result").textContent = "Checking the mainnet custody account…";
  try {
    const result = await request("/api/reserve-observation");
    const raw = BigInt(result.balance_usdc_raw);
    const value = (raw / 1000000n).toLocaleString() + "." + ((raw % 1000000n) / 10000n).toString().padStart(2, "0");
    el("public-result").textContent = "Public vault custody balance: " + value + " USDC at confirmed slot " + result.slot.toLocaleString()
      + ". Demo payments use the saved fork.";
    el("public-account").href = "https://explorer.solana.com/address/" + encodeURIComponent(result.custody);
    el("public-account").hidden = false;
  } catch {
    el("public-result").textContent = "Public reserve data is unavailable. You can still review your demo payment.";
  } finally {
    button.disabled = false;
  }
};
el("download").onclick = () => downloadReceipt(state.load, state.receipt);
function previewSettings() {
  const choice = cashPriority(state, el("profile").querySelector(":checked").value, el("scenario").value);
  for (const input of el("profile").querySelectorAll("input")) {
    const estimate = state.funding_plans[usesCredit(choice) && usesCredit(input.value) ? choice : input.value][el("scenario").value];
    input.labels[0].querySelector("small").textContent = money(estimate.cost_cents) + " plan fee";
  }
  const plan = state.funding_plans[choice][el("scenario").value];
  el("access-help").textContent = profiles[choice].terms;
  el("draft-bill").textContent = money(plan.bill_cents);
  el("draft-cost").textContent = money(plan.cost_cents);
}

for (const [ticket, part] of [["reserve-ticket", "reserve-part"], ["direct-ticket", "direct-part"]]) {
  el(ticket).onclick = () => {
    const source = el(part);
    source.open = !source.open;
    source.querySelector("summary").focus({ preventScroll: true });
  };
}

el("day-gap").onclick = () => {
  const schedule = el("schedule");
  schedule.open = true;
  schedule.querySelector("summary").focus();
  schedule.scrollIntoView({ block: "nearest" });
};
el("configure").onclick = () => {
  for (const input of el("profile").querySelectorAll("input")) input.checked = input.value === (usesCredit(profile) ? "credit line" : profile);
  for (const radio of document.querySelectorAll(".cash-choice input")) radio.checked = radio.value === (profile === "road cash" ? profile : "credit line");
  el("scenario").value = scenario;
  previewSettings();
  el("settings").showModal();
};
el("scenario").onchange = el("profile").onchange = previewSettings;
document.querySelector(".cash-choice").onchange = previewSettings;
el("apply").onclick = () => {
  scenario = el("scenario").value;
  profile = cashPriority(state, el("profile").querySelector(":checked").value, scenario);
  sessionStorage.setItem("funding-profile", profile);
  sessionStorage.setItem("fuel-scenario", scenario);
  render();
  el("settings").close();
};
let recordRequest;

function openRecord(plan) {
  const undo = Boolean(state.manual_payment);
  recordRequest = { undo, body: { version: state.record_version, profile, scenario } };
  el("record").classList.toggle("received", undo);
  el("record-title").textContent = undo ? "Recorded outside payment" : "Record your payment";
  el("record-help").textContent = undo ? "You marked this amount as paid outside FreightOS. Undoing the entry reopens that balance."
    : "Confirm you already paid the fuel vendor outside FreightOS. This updates the bill record only.";
  const rows = [[undo ? "Recorded by you" : "Paid by you outside FreightOS", money(plan.other_funds_cents)], ["Source", profiles[profile].source], ["Load", plan.load]];
  if (state.manual_payment?.reference) rows.push(["Reference", state.manual_payment.reference]);
  facts(el("record-facts"), rows);
  el("reference-field").hidden = undo;
  el("payment-reference").value = "";
  el("statement").hidden = !undo;
  el("save-record").textContent = undo ? "Undo payment entry" : "I paid " + money(plan.other_funds_cents);
  el("record-error").textContent = "";
  el("record").showModal();
}

el("save-record").onclick = async () => {
  const { undo, body } = recordRequest;
  if (!undo) body.reference = el("payment-reference").value.trim();
  await run("Updating bill…", () => request(undo ? "/api/undo-record" : "/api/record-payment", body), el("record-error"));
  if (state && (undo ? !state.manual_payment : state.manual_payment?.profile === body.profile && state.manual_payment?.scenario === body.scenario && (state.manual_payment.reference || "") === body.reference)) {
    el("record").close();
    el("open-payment").focus();
  }
};
el("statement").onclick = async () => {
  const expected = recordRequest.body;
  const feedback = el("record-error");
  try {
    const latest = await request("/api/state");
    const entry = latest.manual_payment;
    if (!entry || latest.record_version !== expected.version || entry.profile !== expected.profile || entry.scenario !== expected.scenario) {
      throw new Error("This bill changed. Reopen the current payment entry.");
    }
    const plan = latest.funding_plans[entry.profile][entry.scenario];
    const received = (latest.receipt?.vendor_received_usdc_raw || 0) / 10000;
    const lines = ["FreightOS · Demo bill statement", "Load " + plan.load, "",
      "Fuel bill: " + money(plan.bill_cents), "Received by fuel company from reserve: " + money(received),
      "Outside payment recorded by driver: " + money(entry.cents), "Source: " + profiles[entry.profile].source];
    if (entry.reference) lines.push("Payment reference: " + entry.reference);
    lines.push("Remaining bill balance: " + money(plan.bill_cents - received - entry.cents),
      "", "Estimated plan fees: " + money(plan.cost_cents), "Estimated savings: " + money(plan.savings_cents));
    saveFile(plan.load + "-bill-statement.txt", lines.join("\r\n") + "\r\n", "text/plain;charset=utf-8");
    feedback.textContent = "Bill statement downloaded.";
  } catch (error) {
    feedback.textContent = error.message;
  }
};
el("return-bill").onclick = () => { el("payment").close(); el("open-payment").focus(); };
el("reserve-receipt").onclick = () => el("payment").showModal();
el("open-payment").onclick = () => {
  const plan = state.funding_plans[profile][scenario];
  if (state.receipt || !plan.payment_cents) return openRecord(plan);
  el("payment").showModal();
  if (state?.deposit && !state.setup_cents[profile] && !state.review && !state.receipt && state.funding_plans[profile][scenario].payment_cents) {
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
  sessionStorage.removeItem("trip-start");
  tripStart.value = "";
  el("payment").close();
});
el("proof").ontoggle = () => { el("details").textContent = el("proof").open && currentStep ? JSON.stringify(currentStep, null, 2) : ""; };
window.addEventListener("hashchange", () => {
  if (!pending && new URLSearchParams(location.hash.slice(1)).has("plan")) sync(true).catch(showRetry);
});
bindWallet(run, sync);
sync(true).catch(showRetry);
