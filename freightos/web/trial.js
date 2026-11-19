import { downloadReceipt, el, facts, money, request, verifySample } from "./common.js";

let selection;
let row;
let loads = [];
let report;
let stressReport;
let stressLoading = false;

async function showCase() {
  for (const cell of el("load-map").children) {
    const selected = cell.value === el("case").value;
    cell.setAttribute("aria-pressed", String(selected));
    cell.tabIndex = selected ? 0 : -1;
  }
  history.replaceState(null, "", "#" + (el("baseline").value === "composition" ? "same-access/" : "") + el("case").value);
  selection?.abort();
  selection = new AbortController();
  const signal = selection.signal;
  row = null;
  el("case-facts").replaceChildren();
  el("sample-result").textContent = "";
  el("verify-sample").hidden = el("verify-sample").disabled = true;
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
      ["Estimated savings", money(row[el("baseline").value + "_savings_cents"])],
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
    el("verify-sample").disabled = false;
    el("verify-sample").hidden = el("receipt").hidden = !row.fork_receipt;
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

function displayBaseline() {
  showStress();
  const same = el("baseline").value === "composition";
  const savings = report[el("baseline").value + "_savings"];
  el("total").textContent = money(savings.total_cents);
  el("baseline-description").textContent = same
    ? "Both strategies can use the reserve. FreightOS also checks combinations with credit. Fees fell on " + savings.positive_cases + " of " + report.cases + " sample loads."
    : "Compared with the cheapest available plan without a reserve, fees fell on " + savings.positive_cases + " of " + report.cases + " sample loads.";
  facts(el("stats"), [
    ["Loads with lower financing costs", savings.positive_cases + " of " + report.cases],
    ["Loads with equal costs", savings.ties + " of " + report.cases],
    ["Median saving per load", money(savings.median_cents)],
    ["Reserve payments signed and replayed", String(report.execution_counts["signed and replayed on fork"])]
  ]);
}

async function showStress() {
  if (!report || !el("stress-test").open || stressLoading) return;
  el("stress-retry").hidden = true;
  try {
    if (!stressReport) {
      stressLoading = true;
      el("adverse").textContent = "Checking the stressed funding plans…";
      stressReport = await request("/api/trial/stress");
    }
    const timing = stressReport.one_day_delay;
    el("delay-only").textContent = "A one-day reserve delay alone leaves " + timing.misses + " planned loads short by "
      + money(timing.shortfall_cents) + " in total at their first missed deadlines. Allowing for that day before choosing funding adds "
      + money(timing.replanning_fee_cents) + " in total fees across the " + report.cases + " simulated loads.";
    const result = stressReport[el("baseline").value];
    el("adverse").textContent = "Add $5 per reserve use, half a percentage point in term costs and one day of delay. After repricing, savings versus the selected comparison total "
      + money(result.savings_cents) + " across " + report.cases + " loads. An unexpected delay after locking the original plans would miss " + report.surprise_delay_misses + " deadlines.";
    el("stress-mix").textContent = "Replace the " + result.replaced_cases + " biggest savers with repeats of the lowest-saving load. The stressed total becomes "
      + money(result.worst_mix_cents) + ". This tests how much the result depends on the mix of carriers.";
  } catch {
    el("adverse").textContent = "The stress comparison could not load.";
    el("stress-mix").textContent = "";
    el("stress-retry").hidden = false;
  } finally {
    stressLoading = false;
  }
}
el("stress-test").ontoggle = el("stress-retry").onclick = showStress;

async function load() {
  report = await request("/api/trial");
  const used = report.loads.filter(item => item.used_reserve).length;
  el("reserve-count").textContent = used;
  el("outside-count").textContent = report.cases - used;
  el("context").textContent = "Choose one of " + report.cases + " loads to inspect its fees and payments.";
  el("baseline").disabled = false;
  el("composition").textContent = "Combining funding sources saved " + money(report.composition_savings.total_cents) + " versus the simpler reserve strategy. A driver making the same choices manually could match these costs.";
  el("assumptions").textContent = report.assumptions;
  loads = report.loads;
  await openLinkedCase();
}

function filterLoads(previous = el("case").value) {
  const query = el("case-search").value.trim().toLowerCase();
  const method = el("case-filter").value;
  const matches = loads.filter(item => {
    if (!item.load.toLowerCase().includes(query)) return false;
    if (method === "savings") return item[el("baseline").value + "_savings_cents"] > 0;
    return method === "all" || item.used_reserve === (method === "reserve");
  });
  if (method === "savings") {
    const key = el("baseline").value + "_savings_cents";
    matches.sort((a, b) => b[key] - a[key] || a.load.localeCompare(b.load));
  }
  el("load-map").replaceChildren(...matches.map(item => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.value = item.load;
    cell.dataset.used = item.used_reserve;
    cell.title = item.load + (item.used_reserve ? " · Used reserve" : " · No reserve payment");
    cell.setAttribute("aria-label", cell.title);
    return cell;
  }));
  el("case").replaceChildren(...matches.map(item => new Option(item.load + (item.used_reserve ? " · Used reserve" : " · No reserve payment"), item.load)));
  el("case-detail").closest("section").hidden = !matches.length;
  el("clear-filters").hidden = !query && method === "all";
  el("match-count").textContent = matches.length ? matches.length + (matches.length === 1 ? " matching load" : " matching loads") : "No matching loads. Clear the search or change the payment method.";
  el("case").disabled = !matches.length;
  if (!matches.length) {
    selection?.abort();
    row = null;
    el("status").textContent = "";
    el("retry").hidden = true;
    history.replaceState(null, "", location.pathname);
    return;
  }
  if (matches.some(item => item.load === previous)) el("case").value = previous;
  return showCase();
}
function showError(error) {
  el("status").textContent = error.message;
  el("retry").hidden = false;
}
el("retry").onclick = () => (el("case").disabled ? load() : showCase()).catch(showError);
el("verify-sample").onclick = () => verifySample(row.load, el("verify-sample"), el("sample-result"), selection.signal);
el("receipt").onclick = () => downloadReceipt(row.load, row.fork_receipt);
el("case").onchange = showCase;
el("clear-filters").onclick = () => {
  el("case-search").value = "";
  el("case-filter").value = "all";
  filterLoads();
  el("case-search").focus();
};
el("case-search").oninput = el("case-filter").onchange = () => filterLoads();
async function openLinkedCase() {
  const [first, second] = location.hash.slice(1).split("/");
  el("baseline").value = !first || first === "same-access" ? "composition" : "conventional";
  displayBaseline();
  el("case-search").value = "";
  el("case-filter").value = "all";
  const linked = first === "same-access" ? second : first;
  await filterLoads(linked);
  if (linked && row?.load === linked) el("load-title").focus();
}

el("baseline").onchange = () => {
  displayBaseline();
  filterLoads();
};
window.addEventListener("hashchange", () => { if (report) openLinkedCase(); });
el("load-map").onclick = event => {
  const cell = event.target.closest("button");
  if (!cell) return;
  el("case").value = cell.value;
  showCase();
  if (event.detail && window.matchMedia("(max-width: 650px)").matches) el("case").focus();
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
