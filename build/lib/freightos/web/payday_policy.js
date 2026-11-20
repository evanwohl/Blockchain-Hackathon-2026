import { el, facts, money, request, saveFile } from "./common.js";

let openBackupLink = ["#fuel-backup", "#cash-split", "#keep-road-cash"].includes(location.hash);

const title = net => net ? money(net) + " early" : "Wait for the full invoice";
const method = plan => plan.kind === "loan" ? "secured loan" : plan.sold_lamports ? "exchange" : "wallet";

function offerSummary(rows) {
  const viable = rows.filter(p => !p.entry_gap && !p.payday_gap && !p.outside_stress);
  viable.sort((a,b) => a.regret_cents - b.regret_cents || b.least_cents - a.least_cents || a.entry_lamports - b.entry_lamports);
  if (viable.length) return method(viable[0]) + " · at least " + money(viable[0].least_cents) + " left in the tested cases";
  const sound = rows.filter(p => !p.payday_gap && !p.outside_stress).sort((a,b) => a.entry_cents - b.entry_cents);
  if (sound.length) return "Needs a " + money(sound[0].entry_cents) + " wallet budget";
  const affordable = rows.filter(p => !p.entry_gap && !p.outside_stress).sort((a,b) => a.payday_gap - b.payday_gap);
  return affordable.length ? "Payday target short by " + money(affordable[0].payday_gap) : "No checked route fits both limits";
}

function renderCashSplits(pair, missing, floor, shortfall, sizing, receive) {
  const choices = [...pair.choices];
  const exact = sizing?.by_receipt[receive];
  let sized;
  if (exact) {
    sized = {...exact, least_cents: exact.least_cents + missing, most_cents: exact.most_cents + missing,
      retained_cents: exact.protected_cents};
    const index = choices.findIndex(p => p.separate_cents === sized.separate_cents);
    if (index < 0) choices.push(sized);
    else choices[index] = sized;
  }
  const rows = el("cash-split-rows");
  rows.replaceChildren();
  const win = sized ? (exact.least_cents >= floor ? sized : null) : choices.find(p => p.least_cents - missing >= floor);
  for (const p of [...choices].sort((a,b) => a.separate_cents - b.separate_cents)) {
    const row = rows.insertRow();
    if (p === win) row.className = "chosen-split";
    for (const cents of [p.separate_cents, p.vendor_cents, p.least_cents - missing]) row.insertCell().textContent = money(cents);
  }
  rows.closest("table").hidden = choices.length < 2;
  const heading = el("cash-decision"), reason = el("cash-reason");
  if (choices.length < 2 && !sized) {
    heading.textContent = choices.length ? "One checked split fits this cash amount" : "More cash is needed for fuel";
    reason.textContent = choices.length ? "The verified payment uses " + money(choices[0].separate_cents) + " of separate cash." :
      "Bring another " + money(shortfall) + " to cover the bill under this wallet limit.";
  } else if (win) {
    el("backup-amount-label").textContent = "Separate cash to use";
    el("backup-gap").textContent = money(win.separate_cents);
    el("backup-sources").textContent = win.vendor_cents ? "The wallet could send " + money(win.vendor_cents) + " to the fuel company. "
      + money(win.wallet_cash_cents) + " comes from wallet cash, " + money(win.reserve_cents) + " from the reserve and "
      + money(win.exchange_cents) + " from selling SOL. At least " + money(win.retained_cents) + " stays for the next stop."
      : "Your separate cash covers the bill in full. The wallet stays untouched.";
    el("backup-balance").textContent = money(win.untouched_cents) + " of your separate cash stays with you at the stop.";
    heading.textContent = "Use " + money(win.separate_cents) + " of your separate cash";
    reason.textContent = "After paying yourself back, " + money(win.least_cents - missing) + " to "
      + money(win.most_cents - missing) + " remains on payday across the tested prices.";
    const other = choices.find(p => p !== win);
    if (sized) {
      reason.textContent = money(win.untouched_cents) + " stays with you at the stop. "
        + "This is the least separate cash that covers fuel and leaves your " + money(floor) + " payday target intact.";
      if (win.one_cent_less) reason.textContent += win.one_cent_less.least_cents === null
        ? " Bringing one cent less would exceed the wallet limit."
        : " Bringing one cent less would leave " + money(win.one_cent_less.least_cents) + " in the weakest case.";
      const full = choices.reduce((a,b) => a.separate_cents > b.separate_cents ? a : b);
      if (full.separate_cents > win.separate_cents && full.least_cents > win.least_cents) reason.textContent +=
        " Using " + money(full.separate_cents) + " instead would leave " + money(full.least_cents - missing) + " on payday in the weakest case.";
    } else if (other.least_cents - missing < floor) reason.textContent += " Using " + money(other.separate_cents)
      + " can leave only " + money(other.least_cents - missing) + ", below your " + money(floor) + " target.";
    else if (win.minimum_edge_cents > 0) reason.textContent += " That is at least " + money(win.minimum_edge_cents)
      + " more than using " + money(other.separate_cents) + " at every tested price.";
  } else {
    heading.textContent = "Fuel is covered. Payday still falls short.";
    reason.textContent = (sizing ? "Even the strongest payday result needs another " : "The stronger of these two splits needs another ") + money(floor + missing - Math.max(...choices.map(p => p.least_cents)))
      + " to meet your payday target in the weakest case.";
  }
  if (sized) {
    const proof = !exact.vendor_cents ? "The bill is paid entirely from separate cash."
      : exact.payment_slot ? "This wallet transfer was simulated on public Solana at slot " + exact.payment_slot.toLocaleString("en-US") + "."
      : "This amount is priced from the pool accounts. Refresh on public Solana to check the exact transfer.";
    el("cash-switch").textContent = "Tested SOL buyback prices range from " + money(pair.low_price_cents) + "–" + money(pair.high_price_cents) + ". " + proof;
    return;
  }
  el("cash-switch").textContent = pair.lasting_price_cents === null ? "" : "The tested SOL prices run from " + money(pair.low_price_cents)
    + " to " + money(pair.high_price_cents) + ". If buying back one SOL costs " + money(pair.lasting_price_cents)
    + " or more, using more separate cash leaves more on payday.";
}

function setupBackup(policy) {
  const panel = el("backup-panel"), cash = el("backup-cash"), refresh = el("backup-refresh");
  const result = el("backup-result"), status = el("backup-status"), receipts = el("backup-invoice");
  const current = () => panel.ontoggle === toggle && !el("policy-result").hidden;
  let displayed, baseSources, busy = false, alignOnOpen = openBackupLink;
  result.hidden = el("backup-proof").hidden = true;
  refresh.disabled = false;
  const compare = el("backup-compare"), splits = el("cash-splits");
  compare.hidden = splits.hidden = true;
  el("backup-payday").hidden = false;
  const cashCents = () => cash.value === "" || !cash.validity.valid ? null : Math.round(cash.valueAsNumber * 100);
  if (openBackupLink && ["#cash-split", "#keep-road-cash"].includes(location.hash)) cash.value = "75.00";
  const invoice = policy.invoice_cents, plan = policy.winner;
  receipts.replaceChildren(new Option("Full invoice · " + money(invoice), invoice));
  if (plan?.customer_cents) {
    if (plan.customer_fee_cents) receipts.add(new Option("Both payments, after the fee · " + money(invoice - plan.customer_fee_cents), invoice - plan.customer_fee_cents));
    receipts.add(new Option("Only the regular installment · " + money(plan.invoice_cents), plan.invoice_cents));
  }
  receipts.add(new Option("Nothing received · " + money(0), 0));
  function payday() {
    if (!displayed) return;
    const missing = invoice - Number(receipts.value);
    const least = displayed.payday.least_cents - missing, most = displayed.payday.most_cents - missing;
    el("backup-payday").textContent = least < 0
      ? "In the weakest tested case, you would need another " + money(-least) + " to restore the money used for fuel."
      : "After paying yourself back, " + money(least) + " to " + money(most) + " stays available across the tested SOL prices.";
    if (cash.validity.valid && cash.value !== "" && Math.round(cash.valueAsNumber * 100) < displayed.outside_cents)
      el("backup-payday").textContent = "This assumes you first cover the fuel shortfall. " + el("backup-payday").textContent;
    if (policy.floor_cents > 0 && least < policy.floor_cents) el("backup-payday").textContent += " The payday target is short by " + money(policy.floor_cents - least) + " in the weakest case.";
  }
  function balance() {
    if (!displayed) return;
    const need = displayed.outside_cents;
    const available = Math.round(cash.valueAsNumber * 100);
    const short = Math.max(0, need - available);
    el("backup-balance").textContent = !cash.validity.valid ? "Enter a cash amount in dollars and cents."
      : cash.value === "" ? "" : short ? money(short) + " would still be due at the stop."
      : "That covers the bill. " + money(available - need) + " of your separate cash stays with you.";
  }
  function cashChoices() {
    if (!displayed) return;
    const amount = cashCents();
    const pair = displayed.cash_comparison;
    const matched = pair && amount === displayed.separate_cents;
    el("backup-amount-label").textContent = "Least separate cash in this check";
    el("backup-gap").textContent = money(displayed.outside_cents);
    el("backup-sources").textContent = baseSources;
    balance();
    const example = cash.value === "" && cash.validity.valid && policy.recorded_backup;
    compare.hidden = Boolean(matched) || !(example || (amount !== null && amount > displayed.outside_cents));
    compare.disabled = busy;
    compare.textContent = example ? "Compare with $75 of separate cash" : amount === 7500 && policy.recorded_backup
      ? "Find the least cash I need" : "Find my cash amount on public Solana";
    splits.hidden = !matched;
    el("backup-payday").hidden = Boolean(matched && pair.choices.length === 2);
    if (!matched) return;
    renderCashSplits(pair, invoice - Number(receipts.value), policy.floor_cents, displayed.outside_cents - amount, displayed.cash_floor, receipts.value);
  }
  async function check(source, separate = null) {
    if (busy) return;
    busy = refresh.disabled = compare.disabled = true;
    status.textContent = source === "live" ? "Checking both exchanges and the fuel reserve on public Solana…" : "Opening the recorded backup…";
    try {
      const q = await request("/api/stop-backup", {source, cap_lamports: policy.available_lamports,
        separate_cents: separate,
        receipt_amounts: separate === null ? null : [...new Set([Number(receipts.value), ...[...receipts.options].map(o => Number(o.value))])],
        reference_cents: policy.reference_cents, stress_cents: policy.stress_cents,
        floor_cents: policy.floor_cents}, AbortSignal.timeout(95000));
      if (!current()) return;
      displayed = q;
      const best = q.best;
      result.hidden = el("backup-proof").hidden = false;
      status.textContent = source === "live" ? "New Devnet simulation · no funds moved" : "Recorded Devnet simulation · no funds moved";
      el("backup-gap").textContent = money(q.outside_cents);
      el("backup-sources").textContent = best ? "The wallet could send " + money(q.covered_cents) + " to the fuel company without borrowing. "
        + money(best.wallet_raw / q.scale) + " comes from wallet cash, " + money(best.reserve_raw / q.scale) + " from the reserve and "
        + money(best.swap_raw / q.scale) + " from selling SOL. " + money(best.retained_raw / q.scale) + " stays for the next stop."
        : "This wallet limit cannot cover the network fee. The full fuel bill needs another source.";
      payday();
      el("backup-terms").textContent = "This assumes the fuel company accepts split payment and the outside cash clears at the stop. "
        + "The check sizes the wallet payment in whole cents, keeps the three-gallon reserve and leaves room for a 0.5% change in exchange cost. "
        + "Payday restores the separate cash, wallet cash, reserve and SOL at the maximum allowed sale cost including the network fee. "
        + "This independent Devnet check used slots " + q.first_slot.toLocaleString("en-US") + "–" + q.last_slot.toLocaleString("en-US") + ".";
      if (q.cash_floor) el("backup-terms").textContent += " FreightOS prices separate cash in one-cent steps using the two Orca pools at slot "
        + q.cash_floor.slot.toLocaleString("en-US") + ". It checks each wallet budget and the cash left at the highest tested buyback price. Each sale uses one pool within its observed liquidity range.";
      else if (q.cash_comparison) el("backup-terms").textContent += " Intermediate amounts could not be verified. Both displayed splits use one wallet and the same observation window. Among splits meeting your payday target throughout the price range, FreightOS minimizes the largest amount left on the table versus the other split, checking cent rounding throughout.";
      if (q.unavailable_pools.length) el("backup-sources").textContent += " A pool declined the payment check. Another route might cover more.";
      baseSources = el("backup-sources").textContent;
      el("backup-download").hidden = source !== "recorded";
      if (q.evidence_path) el("backup-download").href = q.evidence_path;
      cashChoices();
      if (alignOnOpen && ["#cash-split", "#keep-road-cash"].includes(location.hash) && !splits.hidden && document.activeElement === panel.querySelector("summary")) {
        el("cash-decision").focus({preventScroll: true});
        splits.scrollIntoView({block: "center"});
      } else if (alignOnOpen && panel.open && document.activeElement === panel.querySelector("summary"))
        panel.scrollIntoView({block: "start"});
      alignOnOpen = false;
    } catch (error) {
      if (current()) status.textContent = error.message + (displayed ? " The previous backup is still shown (" + displayed.source + " Devnet check)." : " The fuel backup is unconfirmed.");
    } finally {
      busy = false;
      if (current()) { refresh.disabled = false; cashChoices(); }
    }
  }
  function toggle() {
    if (panel.open && policy.recorded_backup && !displayed) check("recorded", cashCents() === 7500 ? 7500 : null);
  }
  status.textContent = "See how much you could pay without borrowing or collecting early. This uses the SOL limit above.";
  cash.oninput = () => { payday(); cashChoices(); };
  receipts.onchange = () => { payday(); cashChoices(); };
  compare.onclick = () => {
    if (cash.value === "") cash.value = "75.00";
    const amount = cashCents();
    balance();
    if (amount !== null) check(policy.recorded_backup && amount === 7500 ? "recorded" : "live", amount);
  };
  refresh.onclick = () => check("live", cashCents());
  panel.ontoggle = toggle;
  if (openBackupLink) {
    openBackupLink = false;
    panel.open = true;
    panel.scrollIntoView({block: "start"});
    panel.querySelector("summary").focus({preventScroll: true});
  }
  if (panel.open && policy.recorded_backup) check("recorded", cashCents() === 7500 ? 7500 : null);
}

export function showPolicy(d) {
  const w = d.winner;
  el("credit-heading").textContent = !w ? "Both limits cannot be met" : w.customer_cents ? "Ask for " + money(w.customer_cents) + " early" : "Keep the full invoice";
  el("policy-label").textContent = w?.customer_cents ? "Fuel covered if customer cash clears" : w ? "Fuel covered at the stop" : "Fuel still to arrange";
  el("policy-total").textContent = money(d.bill_cents);
  el("policy-draft").hidden = !w?.customer_cents;
  el("policy-strip").hidden = !w;
  if (w) {
    const sources = [w.customer_cents, w.cash_used_cents, w.reserve_cents, w.venue_cents];
    el("policy-strip").querySelectorAll("i").forEach((bar,i) => { bar.style.width = (100*sources[i]/d.bill_cents) + "%"; });
    el("policy-action").textContent = (w.customer_cents ? "Confirm the " + money(w.customer_fee_cents) + " fee and wait for the customer’s " + money(w.customer_cents) + " to clear. " : "No early customer payment is needed. ")
      + (w.kind === "loan" ? "Pledge SOL for the remaining loan." : w.sold_lamports ? "Use the exchange to finish the payment without borrowing." : "Use the spendable wallet balance.");
    facts(el("policy-sources"), [["Customer pays before the stop", money(w.customer_cents)], ["Existing wallet cash", money(w.cash_used_cents)],
      ["Fuel reserve", money(w.reserve_cents)], [w.kind === "loan" ? "Borrowed against SOL" : "From selling SOL", money(w.venue_cents)],
      ["Still due after the payments clear", money(0)]]);
    el("policy-payday").textContent = "The customer still owes " + money(w.invoice_cents) + ". After refilling the reserve and restoring spendable funds, the plan leaves "
      + money(w.least_cents) + " to " + money(w.most_cents) + " over the tested price range. " + money(w.reserve_left_cents) + " remains in the vault for the next fuel stop.";
    el("policy-draft").onclick = () => saveFile("FreightOS-early-payment-request.txt", "Invoice [invoice or load ID]\n\nPlease confirm that " + money(w.customer_cents)
      + " can clear before [fuel purchase date]. Including your fee of " + money(w.customer_fee_cents) + ", that leaves " + money(w.invoice_cents)
      + " of the $200 freight invoice due on [regular payment date].\n", "text/plain");
  } else {
    const paying = d.plans.filter(p => !p.entry_gap && !p.outside_stress).sort((a,b) => b.least_cents-a.least_cents);
    const smallest = Math.min(...d.plans.map(p => p.entry_cents));
    el("policy-label").textContent = paying.length ? "Best cash left from freight in the weakest case" : "Smallest checked opening budget";
    el("policy-total").textContent = money(paying.length ? paying[0].least_cents : smallest);
    el("policy-action").textContent = paying.length ? "Fuel can be covered today. The strongest payday result is still " + money(paying[0].payday_gap)
      + " short of the " + money(d.floor_cents) + " target in the weakest price case." : "No checked plan fits both the current wallet and the price range.";
    facts(el("policy-sources"), [["Fuel due before freight pays", money(d.bill_cents)], ["Cash needed from the invoice on payday", money(d.floor_cents)]]);
    el("policy-payday").textContent = "The comparison includes refilling the reserve, repaying debt and replacing wallet assets used for this stop.";
  }
  const wait = d.plans.find(p => !p.customer_cents && p.entry_lamports === d.wait_entry && !p.payday_gap && !p.outside_stress);
  el("policy-wait").textContent = wait ? "To leave the whole invoice on its regular terms, the smallest checked wallet budget is " + money(wait.entry_cents)
    + (wait.entry_gap ? ". That exceeds what is available today." : ". It fits your budget.") : "Waiting for the whole invoice misses the payday target or falls outside the loan stress in the checked routes.";
  const tryWait = el("policy-try-wait");
  tryWait.hidden = !wait || !wait.entry_gap || wait.entry_lamports > d.actual_lamports;
  if (!tryWait.hidden) {
    tryWait.textContent = "Compare with " + money(wait.entry_cents) + " available";
    tryWait.onclick = () => {
      el("policy-budget").value = (wait.entry_cents / 100).toFixed(2);
      el("policy-budget").dispatchEvent(new Event("change", {bubbles: true}));
      el("policy-budget").focus();
    };
  }
  if (w && d.minimum_early_cents < w.customer_cents) el("policy-wait").textContent += " A smaller advance of " + money(d.minimum_early_cents) + " also fits, with a different payday result.";
  facts(el("policy-offers"), [...new Set(d.plans.map(p => p.customer_cents))].map(net => [title(net), offerSummary(d.plans.filter(p => p.customer_cents === net))]));
  el("policy-rule").textContent = w ? "Among plans that meet both limits in every case, this choice leaves at most " + money(w.regret_cents)
    + " less than choosing with the payday price known in advance. The comparison checks every whole-cent price from " + money(Math.min(...d.cases.map(c => c.price_cents))) + " to " + money(Math.max(...d.cases.map(c => c.price_cents))) + ". A tie favors more cash in the weakest case, then less early customer money." : "Only customer offers made available in the form were considered.";
  const history = d.cases.filter(c => c.start);
  el("policy-evidence").textContent = history.length + " historical 30-day moves come from Gemini SOL contract settlements. Applied to the common " + money(d.reference_cents)
    + " reference, they test how much replacing SOL could cost. " + d.excluded.length + " windows crossed an index change and were excluded. The windows overlap and carry no probability weights.";
  facts(el("policy-moves"), d.cases.map(c => [c.label, money(c.price_cents)]));
  el("policy-assumptions").textContent = "Devnet slots " + d.first_slot.toLocaleString("en-US") + "–" + d.last_slot.toLocaleString("en-US") + ". Each plan keeps the Gemini oil-based fuel allowance. Sales include the SOL sold and network fee. Loans assume repayment and collateral release after 30 days at twice the observed rate, within the tested 40% SOL drop. "
    + (d.keep_profile ? "The driver can leave setup money in the loan profile." : "The plan replaces SOL still tied up in the loan profile.") + " Future replacement uses the tested prices. Intermediate liquidation paths, taxes and future yield are outside this comparison. Dollar amounts use the demo scale and exchange prices come from Solana’s test network.";
  el("policy-result").hidden = false;
  setupBackup(d);
}
