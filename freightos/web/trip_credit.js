import { el, facts, money, request, tokenUnits } from "./common.js";

let gap = 0;
let revision = 0;
let protection;
let replayWeek;
let laterPayout;
let backupCheck;

function clear() {
  revision++;
  el("gap-answer").hidden = el("gap-saved").hidden = true;
  el("gap-status").textContent = "";
  el("gap-coverage").hidden = true;
  el("gap-check").disabled = false;
}

export function setFuelGap(cents, alternative = null, {week = null, payout = null, backup = null} = {}) {
  if (gap !== cents || week !== replayWeek || payout !== laterPayout || backup !== backupCheck) el("gap-offer").value = "";
  replayWeek = week;
  laterPayout = payout;
  backupCheck = backup;
  el("usual-gap").hidden = !(week || backup);
  protection = alternative;
  clear();
  gap = cents;
  el("gap-credit").hidden = !gap;
  if (!gap) el("gap-credit").open = false;
  const label = backup ? " weekly backup" : week ? (payout === null ? " gap" : " late-payout gap") + " from the week ending " + week : " fuel gap";
  el("gap-title").textContent = "Could a loan cover the " + money(gap) + label + "?";
}

function show(data) {
  const q = data.loan;
  if (backupCheck) {
    const amount = q?.advance_cents || 0;
    const gaps = backupCheck.records.map(row => row.gap_cents).filter(value => value > 0);
    el("gap-coverage").hidden = false;
    el("gap-coverage").textContent = gaps.length ? "At " + money(amount) + " per stop, this amount would have covered "
      + gaps.filter(value => value <= amount).length + " of the " + gaps.length + " later fills that actually needed backup in the replay. The largest gap left would be "
      + money(Math.max(0, Math.max(...gaps) - amount)) + ". This assumes the same credit was available each time."
      : "Every later replayed fill was already covered with the selected plan.";
  }
  if (!q) {
    el("gap-status").textContent = "This pledge limit cannot cover a cent of the fuel gap. You still need " + money(gap) + ".";
    return;
  }
  el("gap-verdict").textContent = data.remaining_cents ? money(q.advance_cents) + " for fuel · " + money(data.remaining_cents) + " still needed" : "The loan could cover all " + money(q.advance_cents);
  facts(el("gap-facts"), [
    ["SOL held until repayment", (q.pledge_millis / 1000).toLocaleString("en-US") + " SOL"],
    ["Estimated borrowing cost", money(q.cost_cents)],
    ["Amount to repay in " + q.days + " days", money(q.repay_cents)],
    ["Cash for setup and network fee at entry", money(q.entry_cash_cents)]
  ]);
  el("gap-payout").hidden = laterPayout === null;
  if (laterPayout !== null) {
    const used = Math.min(laterPayout, q.repay_cents);
    el("gap-payout").textContent = "If the " + money(laterPayout) + " oil payout arrives by repayment and you use "
      + money(used) + " toward this loan, " + money(q.repay_cents - used)
      + " still comes from other funds. Entry costs are separate.";
  }
  const recheck = el("gap-full-cap");
  const estimate = data.estimated_full_pledge_millis;
  recheck.hidden = !estimate;
  if (estimate) {
    const cap = (estimate / 1000).toFixed(3);
    recheck.textContent = "Recheck the full amount with an estimated " + cap + " SOL limit";
    recheck.onclick = () => {
      el("gap-cap").value = cap;
      check("live");
    };
  }
  const comparison = data.comparison;
  let verdict = "Compare fees once the rest of the fuel payment is funded.";
  if (comparison) {
    const room = comparison.room_cents;
    verdict = room == null ? "Your other offer would need to cost more than " + money(comparison.entry_and_financing_cents) + " to leave any room for savings."
      : room > 0 ? "Your quoted fee leaves " + money(room) + " for repayment, conversion and the cost of locking up SOL before the loan loses its cost advantage."
      : room < 0 ? "Your other offer uses " + money(-room) + " less cash in fees and setup at these terms."
      : "Both choices use the same amount in fees and setup before exit costs.";
  }
  el("gap-comparison").textContent = verdict + " This counts all first-use setup cash upfront and assumes no refund.";
  const alternative = el("gap-protection");
  alternative.hidden = !protection;
  if (protection && data.remaining_cents) {
    alternative.textContent = "The loan leaves a gap. The " + money(protection.total_cents)
      + " protection budget above could cover the modeled fill if the payout arrives in time.";
  } else if (protection) {
    const extraLoanCash = q.repay_cents + q.entry_cash_cents;
    alternative.textContent = "At the full fuel gap, borrowing requires " + money(extraLoanCash)
      + " beyond the current trip plan through payday, including principal and first-use setup. Raising the protection budget uses "
      + money(protection.extra_cents) + " more today and keeps " + (q.pledge_millis / 1000).toLocaleString("en-US")
      + " SOL free. The premium is spent even if oil falls and you never need the loan.";
  }
  el("gap-risk").textContent = "If SOL falls " + q.drop + "%, the estimated debt reaches " + (q.stress_ltv_bps / 100).toFixed(1)
    + "% of the pledge. At " + q.liquidation_pct + "% Kamino could sell collateral. You still owe the loan if freight pays late.";
  el("gap-sale").textContent = "At the observed SOL price of $" + q.sol_price + ", selling enough for " + money(q.advance_cents)
    + " and buying that SOL back on payday would use less cash if the buyback price is below about " + money(q.buyback_break_even_cents)
    + ". Above that price, borrowing could cost less. This assumes the loan stays open, setup uses separate cash, and swaps, taxes and exit costs are zero. The sale price is a reference estimate.";
  el("gap-proof").textContent = "Simulated at Devnet slot " + q.slot.toLocaleString("en-US") + ". The demo vendor received "
    + tokenUnits(q.advance_raw) + " test USDC against " + tokenUnits(q.collateral_lamports / 1000) + " test SOL in the simulation. "
    + "Trip amounts use " + q.model_ratio + "× the test loan and collateral. Entry uses the observed test SOL outlay at $" + q.sol_price
    + " per SOL, without scaling fixed costs. Repayment assumes $1 per USDC and a steady " + (q.rate_bps / 100).toFixed(2) + "% annual rate.";
  el("gap-answer").hidden = false;
  const time = new Date(q.checked_at).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short", timeZone: "UTC"});
  el("gap-status").textContent = (data.source === "recorded" ? "Saved Devnet simulation" : "Public Devnet simulation") + " · " + time + " UTC. No funds moved.";
}

async function check(source) {
  clear();
  const attempt = revision;
  const body = { gap_cents: gap, pledge_millis: Math.round(el("gap-cap").valueAsNumber * 1000),
    days: Number(el("gap-days").value), drop: Number(el("gap-drop").value), source };
  if (el("gap-offer").value !== "") body.offer_cents = Math.round(el("gap-offer").valueAsNumber * 100);
  el("gap-check").disabled = true;
  el("gap-status").textContent = source === "live" ? "Checking the loan and fuel payment on Solana…" : "Opening the saved loan check…";
  try {
    const data = await request("/api/trip-credit", body, AbortSignal.timeout(45000));
    if (attempt === revision) show(data);
  } catch (error) {
    if (attempt !== revision) return;
    el("gap-status").textContent = error.name === "TimeoutError" ? "The network took too long. Try again." : error.message;
    el("gap-saved").hidden = !(source === "live" && [2430, 292].includes(gap) && [100, 1000].includes(body.pledge_millis) && body.days === 23 && body.drop === 40);
  } finally {
    if (attempt === revision) el("gap-check").disabled = false;
  }
}

el("gap-form").oninput = event => {
  if (event.target === el("gap-days")) el("gap-offer").value = "";
  clear();
};
el("gap-form").onsubmit = event => { event.preventDefault(); check("live"); };
el("gap-saved").onclick = () => check("recorded");
