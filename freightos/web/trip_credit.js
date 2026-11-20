import { el, facts, money, request, saveFile, tokenUnits } from "./common.js";
import { creditUse, leastFlex, showSchedule } from "./payday.js";

let gap = 0;
let revision = 0;
let protection;
let replayWeek;
let laterPayout;
let backupCheck;
let priceAdjusted;
let paydayRun;
let weeklyBudget;
let applyFlex;
let savedCheck;

function clear() {
  revision++;
  savedCheck = null;
  el("gap-reschedule").hidden = true;
  el("gap-exit").hidden = el("gap-exit-result").hidden = true;
  el("gap-exit-check").disabled = false;
  el("gap-exit-status").textContent = "";
  el("gap-answer").hidden = el("gap-saved").hidden = true;
  el("gap-status").textContent = "";
  el("gap-coverage").hidden = el("gap-cash-backup").hidden = el("gap-payday").hidden = true;
  el("gap-check").disabled = false;
}

export function setFuelGap(cents, alternative = null, {week = null, payout = null, backup = null, adjusted = false, payday = null, budget = null, onFlex = null} = {}) {
  if (gap !== cents || week !== replayWeek || payout !== laterPayout || backup !== backupCheck || adjusted !== priceAdjusted || payday !== paydayRun) el("gap-offer").value = "";
  replayWeek = week;
  laterPayout = payout;
  backupCheck = backup;
  priceAdjusted = adjusted;
  if (payday?.days !== paydayRun?.days) el("repay-limit").value = "";
  paydayRun = payday;
  weeklyBudget = budget;
  applyFlex = onFlex;
  el("repay-limit-label").hidden = el("repay-limit").disabled = !payday;
  el("gap-days").disabled = !!payday;
  if (payday) el("gap-days").value = String(payday.days);
  el("usual-gap").hidden = !(week || backup || payday);
  protection = alternative;
  clear();
  gap = cents;
  el("gap-credit").hidden = !gap;
  if (!gap) el("gap-credit").open = false;
  const label = payday ? " backup through payday" : adjusted ? " price-adjusted backup" : backup ? " weekly backup" : week ? (payout === null ? " gap" : " late-payout gap") + " from the week ending " + week : " fuel gap";
  el("gap-title").textContent = "Could a loan cover the " + money(gap) + label + "?";
}

function payoffPreview(data) {
  const loan = data.loan;
  if (loan.destination !== "wallet") return;
  const button = el("gap-exit-check");
  const attempt = revision;
  el("gap-exit").hidden = false;
  button.hidden = false;
  button.textContent = "Check what comes back at repayment";
  button.onclick = async () => {
    button.disabled = true;
    el("gap-exit-status").textContent = "Checking the loan payoff and the return of your SOL…";
    try {
      const proof = await request("/api/trip-credit/exit", {collateral_lamports: loan.collateral_lamports,
        advance_raw: loan.advance_raw, days: loan.days, drop: loan.drop, source: data.source}, AbortSignal.timeout(45000));
      if (attempt !== revision) return;
      if (proof.advance_raw !== loan.advance_raw || proof.returned_lamports !== loan.collateral_lamports) throw new Error("Payoff differs from the selected loan.");
      el("gap-exit-title").textContent = "About " + money(proof.refund_cents) + " of setup cash could come back";
      facts(el("gap-exit-facts"), [
        ["Setup cash needed upfront at this check’s price", money(proof.entry_cash_cents)],
        ["Setup cash still held after payoff", money(proof.held_cents)],
        ["Estimated cash cost through payday", money(proof.net_cash_cost_cents)]
      ]);
      let verdict = "The estimate includes borrowing, both network fees and the setup cash still held. It excludes the repaid principal.";
      if (!data.remaining_cents && el("gap-offer").value !== "") {
        const room = Math.round(el("gap-offer").valueAsNumber * 100) - proof.net_cash_cost_cents;
        verdict += room > 0 ? " That leaves " + money(room) + " against your other fee before conversion and the cost of tying up SOL."
          : room < 0 ? " Your other fee is " + money(-room) + " lower." : " This equals your other fee.";
      }
      el("gap-exit-verdict").textContent = verdict;
      el("gap-exit-proof").textContent = "At Devnet slot " + proof.slot.toLocaleString("en-US") + ", the demo borrowed and immediately repaid "
        + tokenUnits(proof.advance_raw) + " test USDC. All " + tokenUnits(proof.returned_lamports / 1000)
        + " test SOL came back. Kamino closed the loan account and kept a separate account for the borrower. "
        + "The estimate uses $" + proof.sol_price + " per SOL and a fee quote for a separate payoff. Interest over "
        + loan.days + " days is projected. Getting the pledge back assumes full repayment before liquidation.";
      el("gap-exit-status").textContent = (proof.source === "recorded" ? "Saved Devnet payoff simulation." : "Public Devnet payoff simulation.") + " No funds moved.";
      el("gap-exit-data").onclick = () => saveFile("FreightOS-payoff-" + proof.slot + ".json", JSON.stringify(proof.evidence), "application/json");
      el("gap-exit-result").hidden = false;
      button.hidden = true;
    } catch (error) {
      if (attempt === revision) el("gap-exit-status").textContent = error.message + " The upfront estimate still applies.";
    } finally {
      if (attempt === revision) button.disabled = false;
    }
  };
}

function show(data) {
  const q = data.loan;
  if (paydayRun) {
    if (q && (q.destination !== "wallet" || q.wallet_gain_raw !== q.advance_raw)) throw new Error("Could not verify the loan proceeds in the fuel wallet.");
    const capacity = q?.advance_cents || 0;
    const entry = q?.entry_cash_cents || 0;
    if (weeklyBudget) {
      const extra = Math.max(0, weeklyBudget.request - capacity);
      const cash = weeklyBudget.contributions;
      const setup = cash[0] + extra + entry + paydayRun.premium_cents;
      const rows = [["Extra cash before the first fill, including setup", money(setup)]];
      if (cash.slice(1).every(v => v === cash[1])) rows.push(["Then before each remaining fill", money(cash[1])]);
      else cash.slice(1).forEach((amount, i) => rows.push(["Before fill " + (i + 2), money(amount)]));
      if (extra) rows.push(["Included at setup to cover the unfunded part", money(extra)]);
      facts(el("gap-payday-cash"), rows);
      const required = extra ? leastFlex(paydayRun.bounds, weeklyBudget.weekly, capacity) : null;
      if (applyFlex && required !== null && required > (weeklyBudget.flex || 0)) {
        const adjust = el("gap-reschedule");
        adjust.hidden = false;
        adjust.textContent = "Move " + money(required) + " of planned cash earlier to fit " + (capacity ? "this loan limit" : "a cash-only plan");
        adjust.onclick = () => {
          applyFlex(required);
          if (paydayRun && gap) check(data.source);
        };
      }
    } else {
      showSchedule(el("gap-payday-cash"), paydayRun, capacity, entry);
    }
    el("gap-payday").hidden = false;
    el("gap-payday-use").hidden = !capacity || !!weeklyBudget || !paydayRun.records.length;
    if (!el("gap-payday-use").hidden) el("gap-payday-history").textContent = creditUse(paydayRun, capacity);
    el("gap-payday-note").textContent = "This counts " + money(capacity)
      + " in loan proceeds once. The setup totals include " + money(q?.entry_cash_cents || 0) + " for loan setup and network fees plus "
      + money(paydayRun.premium_cents) + " for the selected oil protection. Repayment is separate."
      + (data.repayment_limited ? " The full requested loan would exceed your payday repayment limit." : "");
  }
  if (q && weeklyBudget?.schedule && weeklyBudget.flex === null) {
    el("gap-payday-note").textContent += " If you can use the cash-only schedule above, you keep "
      + (q.pledge_millis / 1000).toLocaleString("en-US") + " SOL available and avoid "
      + money(q.cost_cents) + " in estimated borrowing costs and " + money(q.entry_cash_cents) + " in setup outlay.";
  }
  if (backupCheck) {
    const amount = q?.advance_cents || 0;
    const gaps = backupCheck.records.map(row => row.gap_cents).filter(value => value > 0);
    el("gap-coverage").hidden = false;
    el("gap-coverage").textContent = gaps.length ? "At " + money(amount) + " per stop, this amount would have covered "
      + gaps.filter(value => value <= amount).length + " of the " + gaps.length + " later fills that actually needed backup in the replay. The largest gap left would be "
      + money(Math.max(0, Math.max(...gaps) - amount)) + ". This assumes the same credit was available each time."
      : "Every later replayed fill was already covered with the selected plan.";
    const field = priceAdjusted ? "scaled_cents" : "recent_cents";
    const weeks = backupCheck.records.filter(row => row[field] != null);
    if (weeks.length) {
      const cash = weeks.map(row => Math.max(0, row[field] - amount));
      const avg = Math.ceil(cash.reduce((a, b) => a + b, 0) / weeks.length);
      const draws = weeks.filter((row, i) => row.gap_cents > cash[i] && Math.min(row[field], amount) > 0).length;
      const late = weeks.filter((row, i) => row.gap_cents > cash[i]).length;
      el("gap-cash-backup").hidden = false;
      el("gap-cash-backup").textContent = "For this stop, the backup would need " + money(Math.max(0, gap - amount)) + " of your own cash alongside the simulated advance. Over "
        + weeks.length + " replayed stops, keeping only the weekly target above that credit limit in cash would have set aside "
        + money(avg) + " of your money on average. Spending that cash first would have needed a loan on " + draws
        + " stops. If credit was unavailable, " + late + " stops would have run short. Each stop assumes a fresh limit. Setup cash and repayments are separate.";
    }
  }
  if (!q) {
    el("gap-status").textContent = (data.repayment_limited ? "This repayment limit leaves no verified loan." : "This pledge limit leaves no verified loan.")
      + " You still need " + money(gap) + ".";
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
      check();
    };
  }
  const comparison = data.comparison;
  let verdict = "Compare fees once the rest of the fuel payment is funded.";
  if (comparison) {
    const room = comparison.room_cents;
    verdict = room == null ? "With all setup cash counted upfront, your other offer would need to cost more than " + money(comparison.entry_and_financing_cents) + " to leave any room for savings."
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
  el("gap-proof").textContent = "Simulated at Devnet slot " + q.slot.toLocaleString("en-US") + ". " + (q.destination === "wallet" ? "The driver’s wallet retained " : "The demo vendor received ")
    + tokenUnits(q.advance_raw) + " test USDC against " + tokenUnits(q.collateral_lamports / 1000) + " test SOL in the simulation. "
    + "Trip amounts use " + q.model_ratio + "× the test loan and collateral. Entry uses the observed test SOL outlay at $" + q.sol_price
    + " per SOL, without scaling fixed costs. Repayment assumes $1 per USDC and a steady " + (q.rate_bps / 100).toFixed(2) + "% annual rate.";
  payoffPreview(data);
  el("gap-answer").hidden = false;
  const time = new Date(q.checked_at).toLocaleString("en-US", {dateStyle: "medium", timeStyle: "short", timeZone: "UTC"});
  el("gap-status").textContent = (data.source === "recorded" ? "Saved Devnet simulation" : "Public Devnet simulation") + " · " + time + " UTC. No funds moved.";
}

async function check(source = "live") {
  clear();
  const attempt = revision;
  const body = { gap_cents: gap, pledge_millis: Math.round(el("gap-cap").valueAsNumber * 1000),
    days: Number(el("gap-days").value), drop: Number(el("gap-drop").value), source };
  if (paydayRun) {
    body.destination = "wallet";
    if (el("repay-limit").value !== "") body.repay_limit_cents = Math.round(el("repay-limit").valueAsNumber * 100);
  }
  if (el("gap-offer").value !== "") body.offer_cents = Math.round(el("gap-offer").valueAsNumber * 100);
  el("gap-check").disabled = true;
  el("gap-status").textContent = source === "recorded" ? "Checking the saved Devnet loan for the revised amount…"
    : paydayRun ? "Checking the loan into your demo fuel wallet…" : "Checking the loan and fuel payment on Solana…";
  try {
    const data = await request("/api/trip-credit", body, AbortSignal.timeout(45000));
    if (attempt === revision) show(data);
  } catch (error) {
    if (attempt !== revision) return;
    el("gap-status").textContent = error.name === "TimeoutError" ? "The network took too long. Try again." : error.message;
    if (source === "recorded") return;
    try {
      const saved = await request("/api/trip-credit", {...body, source: "recorded"});
      if (attempt === revision) {
        savedCheck = saved;
        el("gap-saved").hidden = false;
      }
    } catch {
      if (attempt === revision) el("gap-saved").hidden = true;
    }
  } finally {
    if (attempt === revision) el("gap-check").disabled = false;
  }
}

el("gap-form").oninput = event => {
  if (event.target === el("gap-days")) el("gap-offer").value = "";
  clear();
};
el("gap-form").onsubmit = event => { event.preventDefault(); check(); };
el("gap-saved").onclick = () => {
  if (savedCheck) show(savedCheck);
  el("gap-saved").hidden = true;
};
