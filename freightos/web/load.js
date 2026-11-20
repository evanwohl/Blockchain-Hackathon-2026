import { setupLoadCredit } from "./load_credit.js";
const form = document.querySelector("#load");
const retry = form.querySelector("button");
const entries = document.querySelector("#entries");
const message = document.querySelector("#message");
const draftKey = "freightos-load-v1";
const dollars = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const money = cents => dollars.format(cents / 100);
let revision = 0;

function inputs(sample = false) {
  const data = Object.fromEntries(new FormData(form));
  if (sample) {
    for (const el of form.elements) {
      if (el.name) data[el.name] = el.tagName === "SELECT" ? el.options[0].value : el.defaultValue;
    }
  }
  for (const el of form.querySelectorAll('input[type="number"]')) {
    const scale = el.name === "pump_mills" ? 1000 : 100;
    data[el.name] = Math.round((sample ? Number(el.defaultValue) : el.valueAsNumber) * scale);
  }
  data.use_offer = !sample && form.elements.use_offer.checked;
  data.use_bank = !sample && form.elements.use_bank.checked;
  data.compare_funding = !sample;
  return data;
}

function explainPlan(plan) {
  if (plan.status === "unfunded") return "Still short " + money(plan.gap_cents) + " after available funding.";
  const sources = plan.funding.map(draw => {
    const amount = money(draw.net_proceeds_cents);
    if (draw.source === "Kamino reserve") return "Use " + amount + " of your reserve. Refill " + amount +
      " on " + form.elements.refill_date.value + " (modeled cost " + money(draw.cost_cents) + ").";
    return "Draw " + amount + " from the bank. Repay " + money(draw.net_proceeds_cents + draw.cost_cents) +
      " on " + form.elements.bank_due.value + ".";
  });
  return (sources.join(" ") || "Scheduled receipts and cash cover fuel.") + " Total fees: " + money(plan.cost_cents) + ".";

}

async function update() {
  const current = ++revision;
  retry.hidden = true;
  message.textContent = "Checking the calendar…";
  try {
    const response = await fetch("/api/load-calendar", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(inputs()),
    });
    const calendar = await response.json();
    if (current !== revision) return;
    if (!response.ok) throw new Error(calendar.error || "Could not check the calendar.");
    entries.replaceChildren(...calendar.rows.map(row => {
      const li = document.createElement("li");
      const date = new Date(row.date + "T12:00:00");
      for (const value of [date.toLocaleDateString("en-US", {month:"short", day:"numeric"}), row.label,
        (row.change_cents > 0 ? "+" : "") + money(row.change_cents), "Balance " + money(row.balance_cents)]) {
        const part = document.createElement("span");
        part.textContent = value;
        li.append(part);
      }
      return li;
    }));
    const fields = form.elements;
    const due = new Date(fields.fuel_date.value + "T12:00:00").toLocaleDateString("en-US", {month:"short", day:"numeric"});
    write("#fuel-label", "Fuel due " + due);
    write("#fuel-total", money(calendar.fuel_cents));
    write("#road-total", money(inputs().floor_cents));
    write("#verdict", calendar.gap_cents ? "Cash gap before funding" : "Covered before funding");
    write("#gap", money(calendar.gap_cents));
    write("#credit-calendar", calendar.gap_cents ? "Your " + money(calendar.fuel_cents) + " fuel bill is short " + money(calendar.gap_cents)
      + " after keeping " + money(inputs().floor_cents) + " for the road." : "Your load covers the fuel bill and your road cash. No fuel loan is needed.");
    write("#summary", calendar.gap_cents ? "To pay for fuel and keep your road cash." : "You can pay for fuel and keep your road cash.");
    const {selected, waiting} = calendar.decision;
    write("#selected-plan", (waiting ? "Taking early pay: " : "") + explainPlan(selected));
    let comparison = waiting ? "Waiting: " + explainPlan(waiting) : "";
    if (waiting?.status === "funded" && selected.status === "funded") {
      const difference = waiting.cost_cents - selected.cost_cents;
      comparison += difference === 0 ? " Both cost the same." :
        " " + (difference > 0 ? "Taking early pay" : "Waiting") + " saves " + money(Math.abs(difference)) + ".";
    }
    write("#wait-plan", comparison);
    const {reserve, backup, backup_terms} = calendar.decision;
    write("#reserve-result", reserve ? "Simulated redeemable balance: " + money(reserve.capacity_cents) +
      ". " + (reserve.note || (fields.reserve_date.value > fields.fuel_date.value ? "Arrives after fuel is due." : "")) : "");
    write("#reserve-backup", backup ? "Without the reserve: " + backup_terms + ". " + explainPlan(backup) : "");
    document.querySelector("#funding-result").hidden = false;
    message.textContent = "Calendar up to date.";
    try { sessionStorage.setItem(draftKey, JSON.stringify(Object.fromEntries(new FormData(form)))); } catch {}
  } catch (error) {
    if (current === revision) {
      write("#verdict", "Forecast unavailable");
      message.textContent = error instanceof TypeError ? "Could not reach the calendar. Try again." : error.message;
      write("#credit-calendar", "The cash calendar is unavailable. Check your load and try again.");
    retry.hidden = false;
    }
  }
}

try {
  const draft = JSON.parse(sessionStorage.getItem(draftKey) || "{}");
  for (const el of form.elements) if (el.type === "checkbox") el.checked = draft[el.name] === "on";
    else if (el.name && typeof draft[el.name] === "string") el.value = draft[el.name];
} catch {}
function write(selector, text) {
  document.querySelector(selector).textContent = text;
}

function refreshControls() {
  const f = form.elements;
  const useReserve = f.reserve_mode.value !== "off";
  document.querySelector("#reserve-terms").hidden = !useReserve;
  for (const field of [f.reserve_date, f.refill_date]) {
    field.disabled = !useReserve;
    field.required = useReserve;
  }
  for (const field of document.querySelectorAll("#bank-terms input")) {
    field.disabled = !f.use_bank.checked;
    field.required = f.use_bank.checked;
  }
  document.querySelector("#bank-terms").hidden = !f.use_bank.checked;
  const eligible = f.surcharge_status.value === "expected" && Number(f.surcharge_cents.value) > 0;
  f.use_offer.disabled = !eligible;
  const active = eligible && f.use_offer.checked;
  for (const field of [f.offer_date, f.offer_fee_cents]) {
    field.disabled = !active;
    field.required = active;
  }
  document.querySelector("#offer-terms").hidden = !active;
  const net = Number(f.surcharge_cents.value) - Number(f.offer_fee_cents.value);
  write("#offer-note", !eligible ? "Enter an agreed, unpaid surcharge to use early payment." :
    active ? "Expected early receipt: " + money(Math.round(net * 100)) : "Compare receiving the surcharge early for a fee.");
  const amounts = [f.opening_cents.value, Number(f.gallon_hundredths.value)*Number(f.pump_mills.value), f.freight_cents.value, f.surcharge_cents.value];
  form.querySelectorAll("summary small").forEach((node, index) => {
    node.textContent = money(Math.round(amounts[index]*100));
  });
  const edited = [...form.querySelectorAll("input,select")].some(el => el.tagName === "SELECT" ?
    el.value !== el.options[0].value : el.type === "checkbox" ? el.checked !== el.defaultChecked : el.value !== el.defaultValue);
  write("#source", edited ? "Edited load" : "Sample load");
}

let timer;
function schedule() {
  clearTimeout(timer);
  revision++;
  entries.replaceChildren();
  document.querySelector("#funding-result").hidden = true;
  for (const id of ["#gap", "#fuel-total", "#road-total"]) write(id, "—");
  write("#summary", "");
  write("#credit-calendar", "Checking your cash calendar…");
  write("#verdict", "Updating forecast");
  refreshControls();
  const invalid = form.querySelector(":invalid");
  if (invalid) {
    write("#credit-calendar", "Check the amounts and dates in your load.");
    write("#verdict", "Check your entries");
    message.textContent = invalid.closest("label").textContent.trim() + ": " + invalid.validationMessage;
    retry.hidden = true;
    return;
  }
  timer = setTimeout(update, 180);
}
form.addEventListener("input", schedule);

const ratecon = document.querySelector("#ratecon");
const rateconStatus = document.querySelector("#ratecon-status");
const rateconFound = document.querySelector("#ratecon-found");
ratecon.addEventListener("change", async () => {
  const file = ratecon.files[0];
  rateconFound.hidden = true;
  rateconFound.replaceChildren();
  if (!file) return;
  const type = file.type || (file.name.toLowerCase().endsWith(".txt") ? "text/plain" : "application/octet-stream");
  rateconStatus.textContent = "Reading " + file.name + "…";
  try {
    const response = await fetch("/api/rate-confirmation", {method: "POST", headers: {"Content-Type": type}, body: file});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The document could not be read");
    for (const [name, value] of Object.entries(result.fields)) {
      const el = form.elements[name];
      if (!el) continue;
      el.value = value;
      el.closest("details")?.setAttribute("open", "");
    }
    for (const item of result.evidence) {
      const row = document.createElement("li");
      row.textContent = item.label + ": " + item.value + " — from “" + item.line + "”";
      rateconFound.append(row);
    }
    for (const note of result.notes) {
      const row = document.createElement("li");
      row.textContent = "Assumption: " + note;
      rateconFound.append(row);
    }
    const opening = form.elements.opening_date;
    if (result.fields.fuel_date && opening.value > result.fields.fuel_date) opening.value = result.fields.fuel_date;
    rateconFound.hidden = false;
    const who = [result.load_id && "load " + result.load_id, result.parties.broker && "from " + result.parties.broker]
      .filter(Boolean).join(" ");
    rateconStatus.textContent = "Filled " + result.evidence.length + " fields" + (who ? " for " + who : "") +
      ". Check them below." + (result.missing.length ? " Not found: " + result.missing.join(", ") + "." : "");
    form.dispatchEvent(new Event("input"));
  } catch (error) {
    rateconStatus.textContent = error.message + ". You can still enter the values by hand.";
  }
});
form.addEventListener("submit", event => { event.preventDefault(); clearTimeout(timer); update(); });
schedule();

const timing = document.querySelector(".fuel-story .story-choices");
const retryStory = document.querySelector("#retry-story");
const explanations = [
  "You keep $100 of your $600 for the road. That leaves $500 for fuel.",
  "On Nov 5, the customer pays $175 after a $5 fee. You can put $675 toward fuel.",
  "The $175 arrives Nov 6. When fuel is due on Nov 5, you still have only $500 to spend.",
];
let projections = [];

function showScene(index) {
  const {fuel_cents, gap_cents} = projections[index];
  const available = fuel_cents - gap_cents;
  const original = fuel_cents - projections[0].gap_cents;
  write("#cash-part", money(original).replace(/\.00$/, ""));
  write("#receipt-part", money(available - original).replace(/\.00$/, ""));
  write("#story-gap", money(gap_cents).replace(/\.00$/, ""));
  write("#story-copy", explanations[index]);
  document.querySelector("#story-cash").style.width = (100 * original / fuel_cents) + "%";
  document.querySelector("#story-receipt").style.width = (100 * (available - original) / fuel_cents) + "%";
}

async function loadStory() {
  retryStory.hidden = true;
  try {
    const sample = inputs(true);
    projections = await Promise.all([sample, {...sample, use_offer:true},
      {...sample, use_offer:true, offer_date:"2026-11-06"}].map(async data => {
      const response = await fetch("/api/load-calendar", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(data)});
      if (!response.ok) throw new Error();
      return response.json();
    }));
    showScene(Number(timing.querySelector(":checked").value));
    timing.disabled = false;
  } catch {
    write("#story-copy", "The sample could not load.");
    retryStory.hidden = false;
  }
}

timing.addEventListener("change", event => showScene(Number(event.target.value)));
retryStory.addEventListener("click", loadStory);
loadStory();

const arrival = document.querySelector("#arrival");
const bankBackup = document.querySelector("#backup-credit");
const ticket = document.querySelector(".funding-ticket");
const retryTicket = document.querySelector("#ticket-retry");
const segments = ticket.querySelectorAll(".ticket-track i");
let ticketRequest = 0;

async function previewTicket() {
  const request = ++ticketRequest;
  ticket.setAttribute("aria-busy", "true");
  retryTicket.hidden = true;
  const offer = document.querySelector("#customer-offer").checked;
  const data = {...inputs(true), compare_funding:true, use_offer:offer, offer_fee_cents:500,
    offer_date:"2026-11-05", reserve_date:arrival.querySelector(":checked").value, refill_date:"2026-11-28",
    use_bank:bankBackup.checked, bank_date:"2026-11-05", bank_due:"2026-11-28",
    bank_cents:30000, bank_bps:200, bank_fixed_cents:0};
  try {
    const forecasts = await Promise.all(["sample", "off"].map(async reserve_mode => {
      const response = await fetch("/api/load-calendar", {method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({...data, reserve_mode})});
      if (!response.ok) throw new Error();
      return response.json();
    }));
    if (request !== ticketRequest) return;
    const candidates = [];
    for (const {decision} of forecasts) {
      for (const [plan, early] of [[decision.waiting, false], [decision.selected, offer]]) {
        if (!plan) continue;
        const draws = plan.funding || [];
        const sources = [early && "Customer early pay", ...draws.map(draw =>
          draw.source === "Kamino reserve" ? "Reserve" : "Bank line")].filter(Boolean);
        const label = sources.join(" + ") || "Cash on hand";
        if (!candidates.some(item => item.label === label && item.plan.status === plan.status &&
            item.plan.gap_cents === plan.gap_cents && item.plan.cost_cents === plan.cost_cents)) {
          candidates.push({label, plan, early});
        }
      }
    }
    candidates.sort((a,b) => Number(b.plan.status === "funded") - Number(a.plan.status === "funded")
      || (a.plan.gap_cents || 0) - (b.plan.gap_cents || 0) || a.plan.cost_cents - b.plan.cost_cents);
    const {plan, label, early} = candidates[0];
    const forecast = forecasts[0];
    const covered = plan.status === "funded";
    const cash = Math.min(forecast.fuel_cents, data.opening_cents - data.floor_cents);
    const amounts = [cash, ...["Kamino reserve","Approved bank line"].map(name =>
      plan.funding?.find(draw => draw.source === name)?.net_proceeds_cents || 0), early ? data.surcharge_cents - data.offer_fee_cents : 0];
    const lines = [["Your cash", cash], ["Your reserve", amounts[1]], ["Bank line", amounts[2]], ["Customer early pay", amounts[3]]]
      .filter(([, amount]) => amount > 0);
    if (!covered) lines.push(["Still needed", plan.gap_cents]);
    write("#flow-total", money(forecast.fuel_cents));
    write("#flow-state", covered ? "Fully covered" : "Short " + money(plan.gap_cents));
    const comparisons = candidates.map(item => {
      const row = document.createElement("li");
      const cost = document.createElement("strong");
      cost.textContent = item.plan.status === "funded" ? money(item.plan.cost_cents) + " fee" : money(item.plan.gap_cents) + " short";
      row.append(item.label, cost);
      return row;
    });
    document.querySelector("#fee-list").replaceChildren(...comparisons);
    const ledger = lines.map(([label, cents]) => {
      const row = document.createElement("div");
      const title = document.createElement("dt");
      const value = document.createElement("dd");
      title.textContent = label;
      value.textContent = money(cents);
      row.append(title, value);
      return row;
    });
    document.querySelector("#ticket-lines").replaceChildren(...ledger);
    segments.forEach((el,i) => { el.style.width = (100 * amounts[i] / forecast.fuel_cents) + "%"; });
    ticket.dataset.state = covered ? "covered" : "short";
    write("#ticket-icon", covered ? "✓" : "!");
    write("#ticket-title", covered ? label + " · " + money(plan.cost_cents) + " fee" : money(plan.gap_cents) + " still needed");
    const next = candidates.slice(1).find(item => item.plan.status === "funded");
    const savings = next ? next.plan.cost_cents - plan.cost_cents : 0;
    write("#ticket-reason", covered ? "$100 kept for the road." +
      (savings > 0 ? " Saves " + money(savings) + " versus " + next.label.toLowerCase() + "." : "") :
      "Available funds fall short by the fuel deadline, even with the lowest-gap option.");
  } catch {
    if (request !== ticketRequest) return;
    document.querySelector("#fee-list").replaceChildren();
    write("#flow-total", "—");
    write("#flow-state", "");
    delete ticket.dataset.state;
    write("#ticket-title","Forecast unavailable");
    write("#ticket-icon","…");
    write("#ticket-reason","Check the sample again.");
    document.querySelector("#ticket-lines").replaceChildren();
    segments.forEach(el => { el.style.width = "0"; });
    retryTicket.hidden = false;
  } finally {
    if (request === ticketRequest) ticket.setAttribute("aria-busy","false");
  }
}
arrival.addEventListener("change",previewTicket);
bankBackup.addEventListener("change",previewTicket);
document.querySelector("#customer-offer").addEventListener("change",previewTicket);
retryTicket.addEventListener("click",previewTicket);
previewTicket();

setupLoadCredit(inputs, form);
