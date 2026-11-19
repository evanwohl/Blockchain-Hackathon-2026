const el = id => document.getElementById(id);
const usd = cents => (cents / 100).toLocaleString("en-US", {style: "currency", currency: "USD"});
let report;
function selected() { return report.rows[Number(el("case").value)]; }
function showCase() {
  const row = selected();
  el("bill").textContent = usd(row.case.bills[0].cents);
  el("draw").textContent = usd(row.reserve_payment_cents);
  el("chosen").textContent = usd(row.decision.cost_cents);
  el("conventional").textContent = usd(row.conventional.cost_cents);
  el("simple").textContent = usd(row.same_access_simple.cost_cents);
  el("outcome").textContent = row.fork_receipt ? "The vendor received " + usd(row.reserve_payment_cents) + " in test USDC on the fork." : "The selected plan covers this bill without withdrawing from the reserve.";
  el("receipt").hidden = !row.fork_receipt;
  el("terms").textContent = JSON.stringify({fuel: row.fuel, calendar: row.case, selected: row.decision, higher_cost_and_later_arrival: row.adverse_plan}, null, 2);
}
el("case").onchange = showCase;
el("receipt").onclick = () => {
  const row = selected();
  const url = URL.createObjectURL(new Blob([JSON.stringify(row.fork_receipt, null, 2)], {type:"application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = row.load + "-settlement.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
fetch("/evidence/carrier-trial").then(r => {
  if (!r.ok) throw new Error("The synthetic trial report could not be loaded.");
  return r.json();
}).then(data => {
  report = data;
  const savings = report.conventional_savings;
  el("n").textContent = report.cases;
  el("total").textContent = usd(savings.total_cents);
  el("median").textContent = usd(savings.median_cents);
  el("composition").textContent = usd(report.composition_savings.total_cents);
  el("executed").textContent = report.execution_counts["signed and replayed on fork"];
  el("skipped").textContent = report.execution_counts["no reserve payment"];
  el("method").textContent = report.comparators.conventional + " The simpler strategy takes the " + report.comparators.same_access_simple[0].toLowerCase() + report.comparators.same_access_simple.slice(1) + " A manual operator who optimizes every combination can match FreightOS.";
  el("adverse").textContent = "Add $5, half a percentage point and one day to every reserve quote. Repricing leaves " + usd(report.adverse_savings.total_cents) + " in modeled savings. If the delay surprises the driver after the original plan is locked, " + report.surprise_delay_misses + " bills miss their cash deadline.";
  el("assumptions").textContent = report.assumptions;
  report.rows.forEach((row, i) => el("case").add(new Option(row.load + " · " + (row.reserve_payment_cents ? "reserve payment" : "no draw"), i)));
  el("case").selectedIndex = report.rows.findIndex(row => row.fork_receipt);
  showCase();
}).catch(error => { el("status").textContent = error.message; });
