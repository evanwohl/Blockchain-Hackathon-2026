import { showPumpValue } from "./pump_value.js";
import { el, facts, money, request } from "./common.js";

const amount = cents => cents == null ? "Pending" : money(cents);
const stamp = value => new Date(value).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short", timeZone: "UTC"}) + " UTC";

function show(data) {
  const selected = data.plans.find(p => p.key === data.primary);
  const cash = data.plans.find(p => p.key === "cash");
  const reserve = Math.max(0, data.reserve.available_cents - data.reserve.road_cents);
  facts(el("fixed-inputs"), [["Starting diesel price", "$" + (data.baseline.mills / 1000).toFixed(3) + "/gal"],
    ["Trip fuel budget", money(data.trip.budget_cents)], ["Reserve available for extra fuel", money(reserve)],
    ["Other pump cash", money(data.trip.pump_cash_cents)], ["Premium paid on paper", money(selected.premium_cents)]]);
  showPumpValue(data.pump_value, data.trip, data.positions);
  const edge = data.edge;
  el("forward-edge").textContent = edge ? "The plan starts saving extra cash when oil settles at $" + edge.minimum_strike
    + " or higher and regional diesel reaches $" + (edge.minimum_pump_mills / 1000).toFixed(3)
    + " per gallon. The most it can save on this fill is " + money(edge.maximum_saving_cents) + " after premium."
    : "Across the quoted positions, cash alone uses the same or less extra money for the fill.";
  el("fixed-proof").textContent = "Positions fixed " + stamp(data.recorded_at) + ". The simulated Kamino withdrawal used Devnet slot "
    + data.reserve.slot.toLocaleString("en-US") + ". The trial uses EIA’s regional average as a fill price. No station purchase is recorded.";
  el("forward-amount").textContent = amount(selected.short_cents);
  const ready = selected.short_cents != null;
  if (ready) {
    const savings = cash.extra_cash_cents - selected.extra_cash_cents;
    el("forward-why").textContent = "Beyond the frozen fuel budget, reserve and pump cash. Including premium, the plan used "
      + (savings ? money(Math.abs(savings)) + (savings > 0 ? " less than" : " more than") : "the same amount as") + " cash alone.";
  } else {
    el("forward-why").textContent = "The comparison needs the November 20 Gemini settlement and the November 23 Midwest diesel price.";
  }
  facts(el("forward-observations"), [["Oil settlement · Nov 20", data.oil ? "$" + Number(data.oil.close).toFixed(4) + "/bbl" : "Awaiting verification"],
    ["Fuel bill · Nov 23", data.diesel ? money(data.bill_cents) : "Awaiting EIA observation"]]);
  const rows = data.plans.map(plan => {
    const row = document.createElement("tr");
    const name = document.createElement("th");
    name.scope = "row";
    name.textContent = plan.name;
    row.append(name);
    for (const value of [plan.premium_cents, plan.payout_cents, plan.extra_cash_cents]) {
      const cell = document.createElement("td");
      cell.textContent = amount(value);
      row.append(cell);
    }
    return row;
  });
  el("forward-plans").replaceChildren(...rows);
  const loan = data.credit?.loan;
  el("forward-credit").textContent = "The frozen backup target was " + amount(selected.backup_cents) + ". "
    + (loan ? "At a 1 SOL pledge limit, an unsigned Devnet simulation covered " + money(loan.advance_cents) + " in modeled trip dollars."
      : "The trial recorded no funded advance.")
    + (ready ? " At that capacity, " + money(selected.after_credit_cents) + " would still need outside funding." : "")
    + " Borrowing creates a repayment obligation. Availability and rates can change before the fill.";
}

async function load(live) {
  el("forward-check").disabled = true;
  try {
    const data = await request(live ? "/api/forward-fill/check" : "/api/forward-fill");
    show(data);
    if (!live && Date.now() >= Date.parse(data.expiry)) return await load(true);
    el("forward-status").textContent = live ? "Checked " + stamp(data.checked_at) + (data.errors.length ? ". Could not verify " + data.errors.join(" and ") + "." : ".")
      : "Showing the decision as recorded.";
  } catch (error) {
    el("forward-status").textContent = error.message;
  } finally {
    el("forward-check").disabled = false;
  }
}

el("forward-check").onclick = () => load(true);
load(false);
