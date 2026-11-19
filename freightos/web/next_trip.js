import { el, money } from "./common.js";

const labels = {
  "later reserve refill": "Wait to refill the reserve",
  "quick pay late freight": "Take the seven-day invoice offer",
  "quick pay late freight backup": "Take the one-day invoice offer"
};

export function renderNextTrip(state, plan, dayLabel, invoiceFunding) {
  const check = state.next_trip;
  const visible = invoiceFunding && Boolean(check?.options.length);
  el("open-next-trip").hidden = el("from-delay-to-expense").hidden = !visible;
  const note = el("expense-note");
  note.hidden = !visible;
  if (!visible) {
    if (el("next-trip").open) el("next-trip").close();
    return;
  }
  const day = dayLabel(check.day);
  const input = el("trip-expense");
  const button = el("apply-expense");
  const key = "next-trip-expense";
  if (input.dataset.record !== state.record_version) {
    input.dataset.record = state.record_version;
    el("next-trip").querySelector("details").open = window.matchMedia("(min-width: 761px)").matches;
    input.value = sessionStorage.getItem(key + "-record") === state.record_version ? sessionStorage.getItem(key) || "" : "";
  }
  el("trip-expense-label").textContent = "How much cash will you need on " + day + "?";
  el("expense-help").textContent = "Assume freight pays a week late. We check the available invoice plans against all "
    + check.budgets + " fuel budgets.";
  el("expense-plans").replaceChildren(...check.options.map(option => {
    const row = document.createElement("tr");
    const label = document.createElement("th");
    label.scope = "row";
    label.textContent = labels[option.profile];
    row.append(label);
    row.insertCell().textContent = money(option.capacity_cents);
    row.insertCell().textContent = money(option.peak_cost_cents);
    return row;
  }));
  function update() {
    const value = input.value.trim();
    const valid = /^(?:(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{0,2})?|\.\d{1,2})$/.test(value);
    const [dollars, fraction = ""] = value.replaceAll(",", "").split(".");
    const cents = valid ? Number(dollars + fraction.padEnd(2, "0")) : NaN;
    const invalid = Boolean(value) && !Number.isSafeInteger(cents);
    input.setAttribute("aria-invalid", String(invalid));
    button.hidden = true;
    button.value = "";
    const result = el("expense-result");
    const title = result.firstElementChild;
    const detail = result.lastElementChild;
    detail.textContent = "";
    result.hidden = !value;
    note.firstElementChild.textContent = "Check cash for my next trip";
    if (!value || invalid) {
      title.textContent = invalid ? "Enter a dollar amount with up to two decimal places." : "";
      return;
    }
    const current = check.options.find(row => row.profile === plan.profile);
    if (current) {
      note.firstElementChild.textContent = current.capacity_cents >= cents
        ? money(cents) + " for the next trip fits on " + day
        : "Next trip is short by " + money(cents - current.capacity_cents) + " on " + day;
    }
    const offer = check.options.find(row => row.capacity_cents >= cents);
    result.classList.toggle("short", !offer);
    if (!offer) {
      const maximum = Math.max(...check.options.map(row => row.capacity_cents));
      title.textContent = "Short by " + money(cents - maximum);
      detail.textContent = "The available plans cover up to " + money(maximum) + " on " + day + ".";
      return;
    }
    const selected = plan.profile === offer.profile;
    title.textContent = labels[offer.profile];
    detail.textContent = (selected ? "Your plan covers " : "Covers ") + money(cents) + " on " + day
      + " across all tested budgets. Estimated cost for this bill is "
      + money(state.funding_plans[offer.profile][plan.scenario].cost_cents) + ".";
    button.value = offer.profile;
    button.hidden = selected;
  }
  input.oninput = () => {
    sessionStorage.setItem(key + "-record", state.record_version);
    sessionStorage.setItem(key, input.value);
    update();
  };
  update();
}
