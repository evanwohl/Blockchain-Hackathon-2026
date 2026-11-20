import {el, facts, money, request, saveFile} from "./common.js";

const scope = "Each 30-day stop uses a separate invoice on the same terms. Freight pays and working balances are restored before the next stop. Market terms and the price scenarios reset each cycle. The profile and collateral-tracking accounts stay open. Each new loan needs a refundable loan-account deposit and a setup transaction. The model excludes overlapping loads, future rewards and liquidation before payday.";

function legs(row) {
  return [["Cash", row.cash_cents], ["Early pay toward fuel", row.advance_cents - row.surplus_cents],
    ["Wallet dollars", row.stable_cents], ["Sell SOL for", row.sale_cents], ["Borrow", row.draw_cents]]
    .filter(([, cents]) => cents).map(([name, cents]) => name + " " + money(cents)).join(" · ");
}

function choiceRows(s) {
  if (s.choice === "unfunded") return [];
  return [["First stop", s.choice === "open" ? s.first : s.debt_free],
    ["Each later stop", s.choice === "open" ? s.later : s.debt_free]];
}

function verdict(s) {
  if (s.open_cents === null && s.first && s.first.payday_cents < s.floor_cents)
    return "The first loan leaves " + money(s.first.payday_cents) + " on payday. Later trips cannot cover a missed " + money(s.floor_cents) + " target on this trip.";
  if (s.open_cents === null) return "Opening a loan cannot cover every stop and meet your payday target under these terms.";
  if (s.debt_free_cents === null) return "Only opening the loan covers every bill and meets your payday target each time.";
  const gap = s.open_cents - s.debt_free_cents;
  const comparison = gap === 0 ? "Both choices leave the same cash. Staying debt-free avoids the loan."
    : (gap > 0 ? "Opening now" : "Staying debt-free") + " leaves " + money(Math.abs(gap)) + " more across the run.";
  const turn = s.break_even_stops;
  return comparison + (turn && turn <= 12 ? " Setup first pulls ahead at " + turn + (turn === 1 ? " stop." : " stops.") : " Setup never pulls ahead within 12 identical stops.");
}

export function showRun(q) {
  const s = q.series, rows = choiceRows(s);
  el("desk-basis").textContent = "Recorded mainnet estimate · Recurring route";
  el("plan-heading").textContent = s.choice === "open" ? "Open the loan on stop one." : s.choice === "debt_free" ? "Keep this run debt-free." : "This run needs more funding.";
  el("desk-split").hidden = false;
  el("desk-split").textContent = s.stops + " fuel stops at " + money(q.bill_cents) + " each. Keep at least " + money(s.floor_cents) + " on every payday.";
  const figure = n => n === null ? "Outside limits" : money(n);
  facts(el("series-totals"), [["Open on stop one", figure(s.open_cents)], ["Stay debt-free", figure(s.debt_free_cents)]]);
  el("series-verdict").textContent = verdict(s) + " Both totals use " + money(q.high_price_cents) + " per SOL on each payday.";
  el("series-funding-title").hidden = el("series-funding").hidden = !rows.length;
  const body = el("series-funding").querySelector("tbody");
  body.replaceChildren();
  if (rows.length) {
    const [, first] = rows[0], [, later] = rows[1];
    const amounts = [["Cash you bring", first.cash_cents, later.cash_cents],
      ["Customer early pay", first.advance_cents - first.surplus_cents, later.advance_cents - later.surplus_cents],
      ["Wallet dollars", first.stable_cents, later.stable_cents], ["Sell SOL for", first.sale_cents, later.sale_cents],
      ["Borrow against SOL", first.draw_cents, later.draw_cents], ["Payoff on day 30", first.repay_cents, later.repay_cents],
      ["Cash left on payday", first.payday_cents, later.payday_cents]];
    for (const [label, a, b] of amounts) {
      if (!a && !b && label !== "Cash left on payday") continue;
      const tr = body.insertRow(), heading = document.createElement("th");
      heading.scope = "row"; heading.textContent = label; tr.append(heading);
      tr.insertCell().textContent = money(a); tr.insertCell().textContent = money(b);
    }
  }
  el("series-prices").hidden = !s.prices.length;
  el("series-prices").querySelector("tbody").replaceChildren(...s.prices.map(price => {
    const tr = document.createElement("tr");
    for (const value of [price.price_cents, price.open_cents, price.debt_free_cents]) tr.insertCell().textContent = money(value);
    return tr;
  }));
  el("series-renewal").hidden = s.renewal_pct === null;
  if (s.renewal_pct !== null) el("series-renewal").textContent = "If only the first stop is booked, each next stop needs at least a " + s.renewal_pct
    + "% chance of renewal for opening now to leave more expected cash at the stress price. This checks whole percentages with the same renewal chance each time.";
  el("series-scope").textContent = scope;
  el("series-save").hidden = !rows.length;
}

export function saveRun(q, reference) {
  const s = q.series, rows = choiceRows(s);
  if (!rows.length) return;
  const loan = q.mainnet.secured_loan;
  const lines = ["FreightOS · Recurring fuel plan", reference || "Recurring route", "Recorded mainnet estimate",
    s.stops + " separate 30-day stops. Fuel bill per stop: " + money(q.bill_cents) + ". Invoice per stop: " + money(q.invoice_cents),
    "Payday target per stop: " + money(s.floor_cents), "Payday stress price: " + money(q.high_price_cents) + "/SOL", verdict(s)];
  for (const [label, row] of rows) {
    lines.push("", label, legs(row), "Customer pays early: " + money(row.advance_cents) + ". Fee: " + money(row.fee_cents)
      + ". Invoice still owed: " + money(row.invoice_balance_cents), "Pledged: " + (row.pledge_lamports / 1e9).toFixed(4) + " SOL. Estimated payoff: " + money(row.repay_cents),
      "Account setup at opening, including extra fee allowance: " + ((row.draw_cents ? loan.setup.refunded_lamports + loan.setup.setup_fee_lamports
        + (label === "First stop" ? loan.setup.retained_lamports : 0) : 0) / 1e9).toFixed(9) + " SOL",
      "Payday after restoring balances: " + money(row.payday_cents));
  }
  for (const [label, cents] of [["Open now", s.open_cents], ["Stay debt-free", s.debt_free_cents]]) {
    lines.push(label + " · total modeled payday cash: " + (cents === null ? "outside limits" : money(cents)));
  }
  for (const p of s.prices) lines.push(money(p.price_cents) + "/SOL: open now " + money(p.open_cents) + ", stay debt-free " + money(p.debt_free_cents));
  if (s.renewal_pct !== null) lines.push("Minimum conditional renewal chance for a strictly higher expected stress-case balance: " + s.renewal_pct + "% (whole percentages).");
  lines.push("", scope, "Recorded slots · Kamino lending " + loan.slot + ", Orca pools " + q.mainnet.slot + ", account deposits " + loan.setup.slot,
    "Planning estimate only. No loan or payment was submitted.");
  saveFile("FreightOS-repeat-run.txt", lines.join("\n"), "text/plain;charset=utf-8");
}


let simulation;
async function showSimulation(live) {
  const button = el("repeat-simulate"), status = el("repeat-proof-result");
  button.disabled = true;
  status.textContent = live ? "Simulating the loan and immediate payoff on public Devnet…" : "Reading the saved Devnet simulation…";
  try {
    const proof = await request("/api/repeat-credit" + (live ? "/simulate" : ""));
    simulation = (live ? "Public Devnet simulation" : "Saved Devnet simulation") + " · slots " + proof.open_slot + "–" + proof.close_slot
      + ". Vendor received " + (proof.vendor_raw / 1e6).toFixed(6) + " test USDC. Payoff released "
      + (proof.returned_rent / 1e9).toFixed(8) + " test SOL from the loan account. The existing profile balance stayed at "
      + (proof.profile_lamports / 1e9).toFixed(7) + " test SOL. No transaction was submitted.";
    status.textContent = simulation;
  } catch {
    status.textContent = "Simulation unavailable. " + (simulation || "You can download the saved evidence.");
  } finally { button.disabled = false; }
}
el("series-proof").ontoggle = () => {
  if (el("series-proof").open && !simulation && !el("repeat-simulate").disabled) showSimulation(false);
};
el("repeat-simulate").onclick = () => showSimulation(true);
