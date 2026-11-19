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
  const include = el("include-return");
  el("trip-return-choice").hidden = !check.options.some(row => row.returnable_cents > 0);
  if (input.dataset.record !== state.record_version) {
    input.dataset.record = state.record_version;
    el("next-trip").querySelector("details").open = window.matchMedia("(min-width: 761px)").matches;
    const saved = sessionStorage.getItem(key + "-record");
    const matchesReturn = state.last_return?.quote.extra_wallet_cents && state.last_return.quote.version === saved
      && state.last_return.id === sessionStorage.getItem(key + "-quote");
    if (matchesReturn) sessionStorage.setItem(key + "-record", state.record_version);
    input.value = saved === state.record_version || matchesReturn ? sessionStorage.getItem(key) || "" : "";
    include.checked = true;
  }
  el("trip-expense-label").textContent = "How much cash will you need on " + day + "?";
  el("expense-help").textContent = "Assume freight pays a week late. We check the available invoice plans against all "
    + check.budgets + " fuel budgets.";
  function update() {
    const value = input.value.trim();
    const valid = /^(?:(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{0,2})?|\.\d{1,2})$/.test(value);
    const [dollars, fraction = ""] = value.replaceAll(",", "").split(".");
    const cents = valid ? Number(dollars + fraction.padEnd(2, "0")) : NaN;
    const options = check.options.map(row => {
      const available = include.checked ? row.returnable_cents : 0;
      const extra = Math.max(0, (Number.isSafeInteger(cents) ? cents : 0) - row.capacity_cents);
      const yieldCost = Math.ceil(Math.min(extra, available) * check.return_yield_bps / 10000);
      return { ...row, available, extra, yieldCost, limit: row.capacity_cents + available, cost: row.peak_cost_cents + yieldCost };
    });
    const priced = value && Number.isSafeInteger(cents);
    const includeYield = priced && options.some(row => row.extra > 0 && row.limit >= cents);
    el("expense-yield-note").hidden = !includeYield;
    el("expense-yield-note").textContent = "Return estimates include potential lost earnings from trip start to "
      + day + " at the recorded Kamino yield. The approval shows vault rounding. Network fees are paid in test SOL.";
    el("expense-plans").replaceChildren(...options.map(option => {
      const row = document.createElement("tr");
      const label = document.createElement("th");
      label.scope = "row";
      label.textContent = labels[option.profile];
      row.append(label);
      const capacity = row.insertCell();
      capacity.textContent = money(option.capacity_cents);
      if (option.available) {
        const conditional = document.createElement("small");
        conditional.textContent = "+ " + money(option.available) + " with return";
        capacity.append(conditional);
      }
      row.insertCell().textContent = priced && option.limit < cents ? "Doesn’t fit" : money(option.cost);
      return row;
    }));
    const invalid = Boolean(value) && !Number.isSafeInteger(cents);
    input.setAttribute("aria-invalid", String(invalid));
    button.hidden = true;
    button.value = "";
    button.dataset.extra = "0";
    const result = el("expense-result");
    const title = result.firstElementChild;
    const detail = result.querySelector("span");
    const comparison = el("expense-comparison");
    comparison.hidden = true;
    comparison.textContent = "";
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
    const offer = options.filter(row => row.limit >= cents)
      .sort((a, b) => a.cost - b.cost || a.extra - b.extra)[0];
    result.classList.toggle("short", !offer);
    if (!offer) {
      const maximum = Math.max(...options.map(row => row.limit));
      title.textContent = "Short by " + money(cents - maximum);
      detail.textContent = "The available plans cover up to " + money(maximum) + " on " + day + (options.some(row => row.available) ? " including available reserve returns." : ".");
      return;
    }
    const selected = plan.profile === offer.profile;
    title.textContent = labels[offer.profile];
    detail.textContent = (selected ? "Your plan covers " : "Covers ") + money(cents) + " on " + day
      + " across all tested budgets. Estimated cost for this bill is "
      + money(state.funding_plans[offer.profile][plan.scenario].cost_cents) + ".";
    if (offer.extra) {
      title.textContent = "Use " + money(offer.extra) + " of spare reserve";
      const total = state.funding_plans[offer.profile][plan.scenario].cost_cents + offer.yieldCost;
      detail.textContent = "Covers your " + money(cents) + " expense and keeps fuel funded. Estimated cost is "
        + money(total) + ", including " + money(offer.yieldCost) + " in missed earnings through " + day + ".";
      const untouched = check.options.find(row => row.capacity_cents >= cents);
      if (untouched) {
        const saved = state.funding_plans[untouched.profile][plan.scenario].cost_cents
          - state.funding_plans[offer.profile][plan.scenario].cost_cents - offer.yieldCost;
        if (saved > 0) {
          comparison.hidden = false;
          comparison.textContent = "Estimated savings of " + money(saved) + " versus leaving the reserve untouched.";
        }
      }
    }
    button.textContent = offer.extra ? "Review the " + money(offer.extra) + " return" : "Use this funding plan";
    button.dataset.extra = String(offer.extra);
    button.value = offer.profile;
    button.hidden = selected && !offer.extra;
  }
  include.onchange = update;
  input.oninput = () => {
    sessionStorage.setItem(key + "-record", state.record_version);
    sessionStorage.setItem(key, input.value);
    update();
  };
  update();
}
