import { downloadReceipt, el, facts, money, request } from "./common.js";

let selection;
let row;

async function showCase() {
  for (const cell of el("load-map").children) {
    const selected = cell.value === el("case").value;
    cell.setAttribute("aria-pressed", String(selected));
    cell.tabIndex = selected ? 0 : -1;
  }
  selection?.abort();
  selection = new AbortController();
  const signal = selection.signal;
  row = null;
  el("case-facts").replaceChildren();
  el("receipt").hidden = el("choices").hidden = el("raw").hidden = true;
  el("terms").textContent = el("status").textContent = "";
  el("retry").hidden = true;
  el("outcome").textContent = "Loading case…";
  try {
    const data = await request("/api/trial/" + encodeURIComponent(el("case").value), undefined, signal);
    if (signal.aborted) return;
    row = data;
    facts(el("case-facts"), [
      ["Fuel bill", money(row.case.bills[0].cents)],
      ["Paid from reserve", money(row.reserve_payment_cents)],
      ["Cost with FreightOS", money(row.decision.cost_cents)],
      ["Lowest cost without a fuel reserve", money(row.conventional.cost_cents)],
      ["Cost using the reserve with early invoice pay", money(row.same_access_simple.cost_cents)]
    ]);
    el("outcome").textContent = row.fork_receipt
      ? "The fuel company received " + money(row.reserve_payment_cents) + " in demo dollars."
      : "The driver could cover this bill without using the reserve.";
    const sources = row.decision.funding.map(item => [item.source, money(item.net_proceeds_cents)]);
    for (const invoice of row.decision.payments) {
      if (invoice.day <= row.case.bills[0].day) sources.push([invoice.invoice + " proceeds", money(invoice.receipt_cents)]);
    }
    sources.unshift(["Opening cash", money(row.case.opening_cash_cents)]);
    facts(el("sources"), sources);
    el("deadline").textContent = "Bill due on day " + row.case.bills[0].day + ". Amounts shown are available funds and may exceed the bill.";
    el("receipt").hidden = !row.fork_receipt;
    el("choices").hidden = el("raw").hidden = false;
    showDetails();
  } catch (error) {
    if (!signal.aborted) {
      el("outcome").textContent = "";
      showError(error);
    }
  }
}

function showDetails() {
  if (!row || !el("raw").open) return;
  el("terms").textContent = JSON.stringify({ fuel: row.fuel, calendar: row.case, selected: row.decision, higher_cost_and_later_arrival: row.adverse_plan }, null, 2);
}

async function load() {
  const report = await request("/api/trial");
  el("trial-count").textContent = report.cases;
  const savings = report.conventional_savings;
  const used = report.loads.filter(item => item.used_reserve).length;
  el("reserve-count").textContent = used;
  el("outside-count").textContent = report.cases - used;
  const payments = report.execution_counts["signed and replayed on fork"];
  el("total").textContent = money(savings.total_cents);
  el("context").textContent = "Choose one of " + report.cases + " loads to see how much it costs to pay for fuel.";
  facts(el("stats"), [
    ["Loads with lower financing costs", savings.positive_cases + " of " + report.cases],
    ["Loads with equal costs", savings.ties + " of " + report.cases],
    ["Median saving per load", money(savings.median_cents)],
    ["Reserve payments signed and replayed", String(payments)]
  ]);
  el("composition").textContent = "Combining funding sources saved " + money(report.composition_savings.total_cents) + " versus the simpler reserve strategy. A driver making the same choices manually could match these costs.";
  el("adverse").textContent = "With an extra $5 fee, half a percentage point in term costs and a one-day delay, choosing a new plan leaves " + money(report.adverse_savings.total_cents) + " in total modeled savings. If the original plan is already locked, " + report.surprise_delay_misses + " bills miss their deadline.";
  el("assumptions").textContent = report.assumptions;
  el("load-map").replaceChildren(...report.loads.map(item => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.value = item.load;
    cell.dataset.used = item.used_reserve;
    cell.title = item.load + (item.used_reserve ? " · Used reserve" : " · No reserve payment");
    cell.setAttribute("aria-label", cell.title);
    return cell;
  }));
  el("case").replaceChildren(...report.loads.map(item => new Option(item.load + (item.used_reserve ? " · Used reserve" : " · No reserve payment"), item.load)));
  el("case").disabled = false;
  await showCase();
}
function showError(error) {
  el("status").textContent = error.message;
  el("retry").hidden = false;
}
el("retry").onclick = () => (el("case").disabled ? load() : showCase()).catch(showError);
el("receipt").onclick = () => downloadReceipt(row.load, row.fork_receipt);
el("case").onchange = showCase;
el("load-map").onclick = event => {
  const cell = event.target.closest("button");
  if (!cell) return;
  el("case").value = cell.value;
  showCase();
};
el("load-map").onkeydown = event => {
  const cells = Array.from(el("load-map").children);
  const index = cells.indexOf(event.target);
  const columns = Number(getComputedStyle(el("load-map")).getPropertyValue("--columns"));
  const next = { ArrowRight: index + 1, ArrowLeft: index - 1, ArrowDown: index + columns, ArrowUp: index - columns, Home: 0, End: cells.length - 1 }[event.key];
  if (index < 0 || next === undefined) return;
  event.preventDefault();
  const target = cells[Math.max(0, Math.min(next, cells.length - 1))];
  target.focus();
  target.click();
};
el("raw").ontoggle = showDetails;
load().catch(showError);
