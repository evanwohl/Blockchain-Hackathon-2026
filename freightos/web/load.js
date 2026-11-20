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
  return data;
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
    write("#verdict", calendar.gap_cents ? "Extra cash needed" : "Extra cash needed · covered");
    write("#gap", money(calendar.gap_cents));
    write("#summary", calendar.gap_cents ? "To pay for fuel and keep your road cash." : "You can pay for fuel and keep your road cash.");
    message.textContent = "Calendar up to date.";
    try { sessionStorage.setItem(draftKey, JSON.stringify(Object.fromEntries(new FormData(form)))); } catch {}
  } catch (error) {
    if (current === revision) {
      write("#verdict", "Forecast unavailable");
      message.textContent = error instanceof TypeError ? "Could not reach the calendar. Try again." : error.message;
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
  for (const id of ["#gap", "#fuel-total", "#road-total"]) write(id, "—");
  write("#summary", "");
  write("#verdict", "Updating forecast");
  refreshControls();
  const invalid = form.querySelector(":invalid");
  if (invalid) {
    write("#verdict", "Check your entries");
    message.textContent = invalid.closest("label").textContent.trim() + ": " + invalid.validationMessage;
    retry.hidden = true;
    return;
  }
  timer = setTimeout(update, 180);
}
form.addEventListener("input", schedule);
form.addEventListener("submit", event => { event.preventDefault(); clearTimeout(timer); update(); });
schedule();

const timing = document.querySelector(".story-choices");
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
