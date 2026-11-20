import {showExit, exitLines} from "./collateral-exit.js";
import {el, facts, money, request, saveFile} from "./common.js";

const scope = "Each 30-day stop uses a separate invoice on the same terms. Freight pays and working balances are restored before the next stop. Market terms and the price scenarios reset each cycle. The profile and collateral-tracking accounts stay open. Each new loan needs a refundable loan-account deposit and a setup transaction. The model excludes overlapping loads, future rewards and liquidation before payday.";

function legs(row) {
  return [["Cash", row.cash_cents], ["Early pay toward fuel", row.advance_cents - row.surplus_cents],
    ["Wallet dollars", row.stable_cents], ["Sell SOL for", row.sale_cents], ["Borrow", row.draw_cents]]
    .filter(([, cents]) => cents).map(([name, cents]) => name + " " + money(cents)).join(" · ");
}

function choiceRows(s) {
  if (s.booking) {
    const chosen = s.booking.choice;
    if (!chosen) return [];
    const start = chosen.open_stop;
    const range = (from, to) => from === to ? "Stop " + from : "Stops " + from + "–" + to;
    if (start === null) return [[range(1, s.stops), s.debt_free]];
    const rows = [];
    if (start > 1) rows.push([range(1, start - 1), s.debt_free]);
    rows.push([range(start, start), s.first]);
    if (start < s.stops) rows.push([range(start + 1, s.stops), s.later]);
    return rows;
  }
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

function timing(stop) {
  return stop === null ? "Stay debt-free" : "Open on stop " + stop;
}

function bookingReason(b, sizing) {
  if (sizing) return "The largest modeled gap to the best funding mix chosen with hindsight is " + money(b.choice.missed_cents)
    + ". Choosing only the loan date gives a " + money(sizing.baseline_cents) + " gap against the same benchmark.";
  if (!b.choice) return "No borrowing date covers the bills and leaves your required payday cash in every tested outcome.";
  if (b.alternatives.length === 1) return "Only this schedule covers each bill and meets your payday target in every tested outcome.";
  return "Across the tested routes and prices, this timing leaves at most " + money(b.choice.missed_cents)
    + " less than the best date you could have picked with hindsight.";
}

function showBooking(s, rows) {
  const b = s.booking, chosen = b.choice;
  el("plan-heading").textContent = !chosen ? "This run needs more funding." : chosen.open_stop === null ? "Keep this run debt-free."
    : chosen.open_stop === 1 ? "Open the loan on stop one." : "Wait until stop " + chosen.open_stop + " to borrow.";
  el("desk-split").textContent = b.booked + " of " + b.stops + " stops booked. Keep at least " + money(s.floor_cents) + " on each payday.";
  el("series-total-label").textContent = "Your first fuel stop";
  const today = rows[0]?.[1];
  facts(el("series-totals"), today ? [["Cash you bring", money(today.cash_cents)], ["Loan today", money(today.draw_cents)]] : []);
  if (s.sizing && today?.draw_cents && today.sale_cents) {
    el("plan-heading").textContent = "Cover the last " + money(today.draw_cents + today.sale_cents) + " in two parts.";
    facts(el("series-totals"), [["Borrow against SOL", money(today.draw_cents)], ["Sell SOL for", money(today.sale_cents)]]);
    el("series-total-label").textContent = "After " + money(today.cash_cents) + " of your cash, " + money(today.stable_cents)
      + " from your wallet and " + money(today.advance_cents - today.surplus_cents) + " in early freight pay";
  }
  const next = chosen?.open_stop;
  el("series-verdict").textContent = (next > 1 ? "If stop " + next + " goes ahead, borrow " + money(s.first.draw_cents)
    + " and repay " + money(s.first.repay_cents) + " when its freight invoice pays. " : "") + (s.sizing && today?.draw_cents ? "Repay " + money(today.repay_cents) + " when freight pays on day 30. " : "") + bookingReason(b, s.sizing);
  const details = el("booking-check");
  details.hidden = !chosen;
  el("booking-method").textContent = "We test every route length from " + b.booked + " to " + b.stops + " stops. Each payday can take any of "
    + b.price_count + " distinct SOL prices from recorded Gemini moves, the opening price and your extra price. We compare every feasible first-loan date and staying debt-free using the quoted funding splits.";
  el("booking-heading").textContent = s.sizing ? "Why this amount?" : "Why this borrowing date?";
  el("booking-caption").textContent = s.sizing ? "Largest cash gap at each start date after comparing loan amounts" : "Cash you could miss versus the best timing for the outcome";
  if (s.sizing) {
    el("booking-method").textContent = "We keep the original timing plans and also price every feasible whole-cent loan with "
      + money(s.sizing.advance_cents) + " in early freight pay. We compare start dates and a loan amount the driver can repeat on later stops. "
      + "The benchmark can change its funding mix at each payday price in hindsight. Both sides face the same collateral, setup costs and payday target.";
    el("booking-method").textContent += " The run can last " + b.booked + " to " + b.stops + " stops, each with " + b.price_count + " tested SOL prices, including recorded Gemini moves.";
  }
  const body = details.querySelector("tbody");
  body.replaceChildren();
  for (const a of b.alternatives) {
    const tr = body.insertRow();
    tr.classList.toggle("selected-timing", a.open_stop === chosen.open_stop);
    tr.insertCell().textContent = timing(a.open_stop) + (s.sizing && a.draw_cents ? " · " + money(a.draw_cents) : "") + (a.open_stop === chosen.open_stop ? " · selected" : "");
    tr.insertCell().textContent = money(a.missed_cents);
  }
  if (chosen) {
    const w = chosen.witness;
    el("booking-witness").textContent = "One case behind that limit: " + w.stops + (w.stops === 1 ? " stop" : " stops")
      + ", with payday SOL at " + w.prices.map(money).join(" → ") + ". Your schedule leaves " + money(w.cash_cents)
      + " across those paydays. The best tested plan for that case leaves " + money(w.cash_cents + chosen.missed_cents) + ".";
  }
}

function showFullRun(q) {
  const s = q.series;
  el("booking-check").hidden = true;
  el("series-total-label").textContent = "Total cash left across the run";
  el("plan-heading").textContent = s.choice === "open" ? "Open the loan on stop one." : s.choice === "debt_free" ? "Keep this run debt-free." : "This run needs more funding.";
  el("desk-split").textContent = s.stops + " fuel stops at " + money(q.bill_cents) + " each. Keep at least " + money(s.floor_cents) + " on every payday.";
  const figure = n => n === null ? "Outside limits" : money(n);
  facts(el("series-totals"), [["Open on stop one", figure(s.open_cents)], ["Stay debt-free", figure(s.debt_free_cents)]]);
  el("series-verdict").textContent = verdict(s) + " Both totals use " + money(q.high_price_cents) + " per SOL on each payday.";
  const openingPrice = s.prices.find(p => p.price_cents === q.reference_cents);
  if (openingPrice && (s.choice === "open") !== (openingPrice.open_cents > openingPrice.debt_free_cents)) {
    const gap = openingPrice.open_cents - openingPrice.debt_free_cents;
    el("series-verdict").textContent += " At the recorded " + money(q.reference_cents) + " opening price, "
      + (gap === 0 ? "the choices tie." : (gap > 0 ? "opening the loan" : "staying debt-free") + " leaves " + money(Math.abs(gap)) + " more.");
  }
}

export function showRun(q, inputs) {
  const s = q.series, rows = choiceRows(s);
  el("desk-basis").textContent = "Recorded mainnet estimate · Recurring route";
  el("desk-split").hidden = false;
  if (s.booking) showBooking(s, rows);
  else showFullRun(q);
  el("size-route").hidden = !s.booking?.choice;
  el("size-route").textContent = s.sizing ? "Compare timing only" : "Compare loan amounts too";
  const swap = rows[0]?.[1].swap;
  el("booking-swap").hidden = !swap;
  if (swap) el("booking-swap").textContent = "The recorded Orca quote allows up to " + (swap.max_lamports / 1e9).toFixed(9)
    + " SOL to receive " + money(rows[0][1].sale_cents) + " in USDC. Pool quotes and Kamino lending terms are recorded.";
  const opening = s.booking?.choice?.open_stop;
  el("booking-collateral").hidden = !opening;
  if (opening) {
    const setup = q.mainnet.secured_loan.setup;
    el("booking-collateral").textContent = "On stop " + opening + ", pledge " + (s.first.pledge_lamports / 1e9).toFixed(4)
      + " SOL. Allow another " + ((setup.deposit_lamports + setup.setup_fee_lamports) / 1e9).toFixed(8)
      + " SOL for loan account setup and its transaction. A large SOL price drop can put the collateral at risk before freight pays.";
  }
  el("series-funding-title").hidden = el("series-funding").hidden = !rows.length;
  const body = el("series-funding").querySelector("tbody");
  body.replaceChildren();
  if (rows.length) {
    const head = el("series-funding").tHead.rows[0];
    head.replaceChildren();
    for (const name of ["Source", ...rows.map(([label]) => label)]) {
      const th = document.createElement("th"); th.scope = "col"; th.textContent = name; head.append(th);
    }
    const amounts = [["Cash you bring", r => r.cash_cents], ["Customer early pay", r => r.advance_cents - r.surplus_cents],
      ["Wallet dollars", r => r.stable_cents], ["Sell SOL for", r => r.sale_cents], ["Borrow against SOL", r => r.draw_cents],
      ["Payoff on day 30", r => r.repay_cents], ["Cash left at highest SOL price", r => r.payday_cents]];
    for (const [label, read] of amounts) {
      const values = rows.map(([, row]) => read(row));
      if (!values.some(Boolean) && label !== "Cash left at highest SOL price") continue;
      const tr = body.insertRow(), heading = document.createElement("th");
      heading.scope = "row"; heading.textContent = label; tr.append(heading);
      for (const value of values) tr.insertCell().textContent = money(value);
    }
  }
  const picker = el("funding-stage");
  picker.replaceChildren(...rows.map(([label], i) => new Option(label, String(i + 1))));
  el("funding-stage-field").hidden = !s.booking || rows.length < 2;
  el("series-funding").classList.toggle("pick-stage", !el("funding-stage-field").hidden);
  picker.onchange = () => {
    for (const tr of el("series-funding").rows)
      for (const cell of tr.cells) cell.classList.toggle("other-stage", cell.cellIndex > 0 && cell.cellIndex !== Number(picker.value));
  };
  picker.onchange();
  el("series-prices").hidden = !!s.booking || !s.prices.length;
  el("series-prices").querySelector("tbody").replaceChildren(...s.prices.map(price => {
    const tr = document.createElement("tr");
    for (const value of [price.price_cents, price.open_cents, price.debt_free_cents]) tr.insertCell().textContent = money(value);
    return tr;
  }));
  el("series-renewal").hidden = !!s.booking || s.renewal_pct === null;
  if (s.renewal_pct !== null) el("series-renewal").textContent = "If only the first stop is booked, each next stop needs at least a " + s.renewal_pct
    + "% chance of renewal for opening now to leave more expected cash at the stress price. This checks whole percentages with the same renewal chance each time.";
  showExit(rows, q.low_price_cents, inputs);
  el("split-proof").hidden = !s.sizing;
  el("series-proof").hidden = !!s.sizing;
  el("series-scope").textContent = scope;
  el("series-save").hidden = !rows.length;
}

export function saveRun(q, reference) {
  const s = q.series, rows = choiceRows(s);
  if (!rows.length) return;
  const loan = q.mainnet.secured_loan;
  const lines = ["FreightOS · Recurring fuel plan", reference || "Recurring route", "Recorded mainnet estimate",
    s.stops + " separate 30-day stops. Fuel bill per stop: " + money(q.bill_cents) + ". Invoice per stop: " + money(q.invoice_cents),
    "Payday target per stop: " + money(s.floor_cents), "Payday stress price: " + money(q.high_price_cents) + "/SOL", s.booking ? bookingReason(s.booking, s.sizing) : verdict(s)];
  if (s.booking) {
    lines.push(s.booking.booked + " confirmed stops. Selected timing: " + timing(s.booking.choice.open_stop));
    const w = s.booking.choice.witness;
    lines.push("At " + w.stops + " stops and payday SOL prices of " + w.prices.map(money).join(", ")
      + ", this schedule leaves " + money(w.cash_cents) + " versus " + money(w.cash_cents + s.booking.choice.missed_cents)
      + " with the best tested plan for that case. Collateral remains exposed to price drops before payoff.");
    if (s.sizing) lines.push("Additional repeat splits use " + money(s.sizing.advance_cents) + " early pay. The hindsight benchmark can reselect a feasible split at every stop.");
    for (const a of s.booking.alternatives) lines.push(timing(a.open_stop) + " · maximum missed payday cash: " + money(a.missed_cents));
  }
  for (const [label, row] of rows) {
    if (row.swap) lines.push("", label + " · recorded Orca quote", ...row.swap.legs.map(l => l.pool + " · receive " + money(l.output_cents)), "Maximum swap input: " + row.swap.max_lamports + " lamports");
    lines.push("", label, legs(row), "Customer pays early: " + money(row.advance_cents) + ". Fee: " + money(row.fee_cents)
      + ". Invoice still owed: " + money(row.invoice_balance_cents), "Pledged: " + (row.pledge_lamports / 1e9).toFixed(4) + " SOL. Estimated payoff: " + money(row.repay_cents),
      "Account setup at opening, including extra fee allowance: " + ((row.draw_cents ? loan.setup.refunded_lamports + loan.setup.setup_fee_lamports
        + (row === s.first ? loan.setup.retained_lamports : 0) : 0) / 1e9).toFixed(9) + " SOL",
      "Payday after restoring balances: " + money(row.payday_cents), ...exitLines(row));
  }
  for (const [label, cents] of s.sizing ? [] : [["Open now", s.open_cents], ["Stay debt-free", s.debt_free_cents]]) {
    lines.push(label + " · total at the highest SOL price: " + (cents === null ? "outside limits" : money(cents)));
  }
  for (const p of s.prices) lines.push(money(p.price_cents) + "/SOL: open now " + money(p.open_cents) + ", stay debt-free " + money(p.debt_free_cents));
  if (!s.booking && s.renewal_pct !== null) lines.push("Minimum conditional renewal chance for a strictly higher expected stress-case balance: " + s.renewal_pct + "% (whole percentages).");
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


let splitProof;
async function showSplitProof(live) {
  const status = el("split-proof-result"), button = el("split-simulate");
  button.disabled = true;
  status.textContent = live ? "Checking the combined payment and the declined swap on public Devnet…" : "Reading the saved simulation…";
  try {
    const p = await request("/api/split-payment" + (live ? "/simulate" : ""));
    const units = raw => (raw / 1e6).toFixed(4);
    facts(el("split-receipt"), [["Kamino loan", units(p.loan_raw)], ["Orca sale", units(p.sale_raw)], ["Vendor received", units(p.vendor_raw)]]);
    el("split-receipt").hidden = false;
    splitProof = (live ? "Public Devnet simulation" : "Saved Devnet simulation") + " · slot " + p.slot
      + ". One transfer paid the vendor in test USDC. At slot " + p.rollback_slot
      + ", a one-lamport limit on the SOL sale made Orca decline the swap. The loan and payment both rolled back. No transaction was submitted.";
    status.textContent = splitProof;
  } catch {
    status.textContent = "Simulation unavailable. " + (splitProof || "The saved evidence is available to download.");
  } finally { button.disabled = false; }
}
el("split-proof").ontoggle = () => {
  if (el("split-proof").open && !splitProof && !el("split-simulate").disabled) showSplitProof(false);
};
el("split-simulate").onclick = () => showSplitProof(true);
