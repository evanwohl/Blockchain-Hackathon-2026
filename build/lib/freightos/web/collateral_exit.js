import {el, facts, money, request} from "./common.js";

const minSol = raw => (Math.floor(raw / 1000) / 1e6).toFixed(6) + " SOL";
const maxSol = raw => (Math.ceil(raw / 1000) / 1e6).toFixed(6) + " SOL";
const initialView = new URLSearchParams(location.search).get("view");
let jumpToPayoff = ["payoff", "cash-budget", "midtrip", "downside", "pledge", "sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView);
const openMidtrip = ["midtrip", "downside", "pledge", "sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView);
if (["pledge", "sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView)) el("midtrip-topup").value = "250000000";
if (["downside", "pledge", "sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView)) el("midtrip-drop").value = "3000";
if (initialView === "sale-limit") el("sale-policy").value = "historical";
if (["sale-replay", "split-pledge"].includes(initialView)) {
  el("sale-policy").value = "budget";
  el("sale-later").open = initialView === "sale-replay";
}
if (openMidtrip) el("midtrip").open = true;
if (["sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView)) el("midtrip-sale").open = true;
if (["midtrip", "downside", "pledge", "sale", "sale-limit", "sale-replay", "split-pledge", "cash-budget"].includes(initialView)) el("exit-keep").value = "cash";

let focusMidtrip = openMidtrip;
let budgetResult, pending, budgetRevision = 0;
const custom = () => el("exit-keep").value === "cash";
const cashInput = el("cash-exit-budget");
const budgetCents = () => cashInput.validity.valid ? Math.round(cashInput.valueAsNumber * 100) : null;
const loanKey = r => [r.draw_cents, r.repay_cents, r.pledge_lamports].join("/");

export function clearExit() {
  budgetRevision++;
  pending?.abort();
  pending = budgetResult = null;
  el("cash-exit-status").textContent = "Check your budget to see the current payoff.";
}

function budgetQuote(row) {
  return budgetResult?.cents === budgetCents() ? budgetResult.rows.get(loanKey(row)) : null;
}

const checkpoint = el("midtrip-price");
const percent = bps => (bps / 100).toFixed(2) + "%";

function showEarlyPayment(q, row, inputs) {
  const amounts = el("midtrip-amounts"), verdict = el("midtrip-verdict"), risk = el("midtrip-risk");
  el("midtrip-intro").textContent = cashInput.validity.valid
    ? "If your " + money(budgetCents()) + " is available halfway through the trip, see how much to put toward the fuel loan."
    : "Enter the cash available for this trip in the payoff budget above.";
  el("midtrip-trigger").textContent = q ? "At these terms, you can keep all your cash if the day-15 SOL price is "
    + money(q.no_payment_price_cents) + " or higher. Below " + money(q.budget_price_cents)
    + ", the plan cannot maintain that cushion with this budget." : "";
  el("midtrip-limits").hidden = !q;
  el("midtrip-history").hidden = !q?.eligible;
  if (q?.eligible) {
    const trials = q.later_trials;
    el("midtrip-audit").textContent = trials.length ? "Using only price paths that ended before each later history began, the rule missed the cushion in "
      + trials.filter(t => t.missed).length + " of " + trials.length + " cases. Debt reached the liquidation limit in "
      + trials.filter(t => t.reached_liquidation).length + ". These cases use the same assumed price, cash budget and loan terms. The later histories can overlap."
      : "This history has no later window with a completed earlier comparison.";
  }
  if (q?.eligible && q.downside && q.later_trials.length) {
    const missed = q.later_trials.filter(t => t.history_missed).length;
    const extraCash = q.later_trials.map(t => t.payment_cents - t.history_cash_cents);
    const min = Math.min(...extraCash), max = Math.max(...extraCash);
    el("midtrip-audit").textContent += " Without your extra fall assumption, the earlier rule missed " + missed
      + " of those same cases. Including it used " + money(min) + (min === max ? "" : " to " + money(max))
      + " more day-15 cash per case. This comparison is retrospective.";
  }
  el("downside-cost").hidden = !q?.eligible || !q.downside;
  if (q?.eligible && q.downside) {
    const d = q.downside;
    const more = q.needed_cents - d.history_needed_cents;
    el("downside-cost").textContent = "A further " + (d.drop_bps / 100) + "% fall puts SOL at "
      + (d.floor_cents ? money(d.floor_cents) : "less than $0.01") + ". "
      + (more ? "Covering that price needs " + money(more) + " more early cash than the " + money(d.history_needed_cents) + " required by history alone."
        : "The payment required by the recorded paths already covers that price.");
    el("midtrip-trigger").textContent += q.cash_floor_cents
      ? " With all eligible cash paid early, " + money(q.cash_floor_cents) + " is the lowest whole-cent SOL price the pledge can sustain through payday at the modeled rate."
      : " The available cash can repay the entire modeled loan early.";
  }
  showSale(q, row, inputs);
  setDownsideProof(q, row, inputs);
  el("pledge-compare").hidden = el("pledge-tradeoff").hidden = true;
  amounts.hidden = !q?.eligible;
  risk.textContent = "";
  if (!q) {
    verdict.textContent = pending ? "Checking the remaining trip…" : "Check the price and cash budget for this trip.";
    return;
  }
  if (!q.eligible) {
    verdict.textContent = "The loan may already be eligible for liquidation at this price. Check the open loan before relying on a repayment plan.";
    risk.textContent = "Debt would be " + percent(q.current_ltv_bps) + " of the pledge’s value. The recorded limit is " + percent(q.limit_bps) + ".";
    return;
  }
  verdict.textContent = q.short_cents ? "The early cash falls " + money(q.short_cents) + " short of the tested cushion."
    : q.payment_cents ? "Pay " + money(q.payment_cents) + " on day 15 to keep the full pledge within the tested cushion."
    : "You can keep your cash. The loan stays within the tested cushion without an early payment.";
  facts(amounts, [["Cash left from your budget", money(q.unused_cents)], ["Still due on day 30", money(q.payday_cents)]]);
  risk.textContent = "Without an early payment, debt reaches " + percent(q.wait_peak_bps) + " of the pledge’s value across the "
    + q.windows + " historical paths" + (q.downside ? " and your extra fall" : "") + ". With " + money(q.payment_cents) + " paid, the peak is " + percent(q.paid_peak_bps)
    + ". At " + percent(q.limit_bps) + ", Kamino can sell pledged SOL.";
  if (q.topup?.eligible) showPledge(q);
  else if (q.topup) {
    verdict.textContent = "The recorded collateral shares place the loan at the liquidation limit. Check the open loan before adding SOL.";
    amounts.hidden = true;
  }
}

const pledgedSol = raw => (raw / 1e9).toLocaleString("en-US", {maximumFractionDigits: 4}) + " SOL";

let saleRequest, saleKey = "", saleRevision = 0;

function showSale(q, row, inputs) {
  const t = q?.topup, s = t?.sale, panel = el("midtrip-sale");
  panel.hidden = !s;
  const split = s?.split, active = split || s;
  const data = active?.preview ? {plan: {...inputs, payoff_cash: budgetCents(), midtrip_price: q.price_cents,
    midtrip_drop_bps: q.downside?.drop_bps || 0, midtrip_topup_lamports: t.cap_lamports, sale_policy: s.policy, sale_loss_cents: s.loss_limit_cents},
    opening_lamports: row.opening_lamports, ...active.preview} : null;
  const key = JSON.stringify(data);
  if (key !== saleKey) {
    saleKey = key;
    saleRequest = data;
    saleRevision++;
    el("sale-receipt").textContent = "";
    el("sale-live").disabled = false;
  }
  if (!s) return;
  const unavailable = !active.max_lamports;
  el("sale-stress").closest("details").querySelector("summary").textContent = split ? "What if I sold the full amount?" : "What if SOL falls or recovers?";
  for (const id of ["sale-amounts", "sale-limit", "sale-crossover"]) el(id).hidden = unavailable;
  el("sale-stress").closest("details").hidden = !s.max_lamports;
  el("sale-budget").hidden = s.policy !== "budget";
  const minimum = s.outcomes.carry?.minimum_loss_limit_cents;
  const button = el("sale-minimum");
  button.hidden = minimum == null || s.modeled_fill || minimum === s.loss_limit_cents;
  if (!button.hidden) {
    button.dataset.cents = String(minimum);
    button.textContent = split ? "Compare selling the full " + money(s.output_cents) + " at a " + money(minimum) + " limit" : "Compare a " + money(minimum) + " downside limit";
  }
  el("sale-fill").hidden = unavailable;
  const trials = s.later_trips || [];
  el("sale-later").hidden = !trials.length;
  if (trials.length) {
    const fills = trials.filter(t => t.action === "sale").length;
    const mixes = trials.filter(t => t.split_option).length;
    const pledges = trials.filter(t => t.action === "pledge" && !t.split_option).length;
    el("sale-later-summary").textContent = "The full sale fit in " + fills + " of " + trials.length
      + " later trips. " + (mixes ? mixes + " used a smaller sale and pledge. " : "")
      + (pledges ? pledges + " used only the pledge. " : "") + "Payday amounts below are relative to pledging.";
    const misses = trials.filter(t => (t.split_option || t).cushion_missed).length;
    if (misses) el("sale-later-summary").textContent += " The selected plan crossed the target debt cushion on " + misses + ".";
    facts(el("sale-later-results"), trials.map(t => {
      const amount = (t.split_option || t).difference_cents;
      const action = t.split_option ? "Split" : "Sold";
      const result = t.action === "no_sale_plan" ? "No sale to compare" : amount == null ? "Liquidation threshold reached"
        : t.action === "pledge" && !t.split_option ? "Pledged · same result" : amount === 0 ? action + " · even to the cent"
        : action + " · " + money(Math.abs(amount)) + (amount > 0 ? " ahead" : " behind");
      return [t.start, result];
    }));
  }
  if (unavailable) {
    el("sale-title").textContent = "Keep the pledge option";
    el("sale-result").textContent = "A sale within the selected payday limit could not be verified. You can compare the regular sale or keep the pledge plan.";
    el("sale-proof").hidden = true;
    return;
  }
  if (split) {
    el("sale-title").textContent = "Keep " + money(split.unused_cents) + " with a smaller sale";
    el("sale-result").textContent = "At your " + money(split.loss_limit_cents) + " payday downside limit, sell just enough SOL for "
      + money(split.output_cents) + " and pledge " + pledgedSol(split.deposit_limit_lamports) + ".";
    el("sale-fill").textContent = split.cash_cents ? "Add " + money(split.cash_cents) + " of your cash to repay "
      + money(split.payment_cents) + " today." : "The sale covers the entire early payment. Your road cash stays available.";
    facts(el("sale-amounts"), [["Cash left for the road", money(split.unused_cents)], ["Due on payday with the split", money(split.payday_cents)],
      ["Due if you only add collateral", money(t.payday_cents)]]);
    el("sale-limit").textContent = "The swap can spend up to " + (split.max_lamports / 1e9) + " SOL. Even at that limit the split leaves "
      + minSol(split.freed_lamports) + " more unlocked than the pledge plan. Network fees are separate.";
  } else {
    el("sale-title").textContent = !s.modeled_fill
      ? "Which sale price would keep " + money(t.unused_cents) + " for the road?"
      : "Sell some SOL and keep the same " + money(t.unused_cents);
    el("sale-result").textContent = "Sell enough spare SOL for " + money(s.output_cents)
      + (s.cash_cents ? " and add " + money(s.cash_cents) + " of road cash." : ". Your road cash stays untouched.")
      + " The proceeds pay down the fuel loan immediately.";
    if (s.policy === "historical" && s.min_net_price_cents > q.price_cents) {
      el("sale-result").textContent = "This sale needs about " + money(s.min_net_price_cents)
        + " per SOL after swap costs. At your " + money(q.price_cents) + " estimate, keep the pledge option available. Recheck the loan before acting if prices move.";
    }
    if (s.policy === "budget") {
      el("sale-result").textContent = "With a " + money(s.loss_limit_cents) + " downside limit, "
        + (s.modeled_fill ? "the modeled sale fits." : "keep the pledge option available.")
        + " Both choices leave " + money(t.unused_cents) + " for the road.";
    }
    el("sale-fill").textContent = s.modeled_input_lamports == null
      ? "The recorded pools lack enough verified liquidity for this sale."
      : "At " + money(q.price_cents) + " per SOL, the modeled Orca swap needs about "
        + (s.modeled_input_lamports / 1e9).toFixed(6) + " SOL for " + money(s.output_cents) + " after swap costs. "
        + (s.modeled_fill ? "That fits the sale limit." : "That exceeds the sale limit.")
        + (button.hidden ? "" : " The smallest payday downside limit that fits this estimate is " + money(minimum) + ".");
    facts(el("sale-amounts"), [["Cash left for the road", money(t.unused_cents)], ["Due on payday after selling", money(s.payday_cents)],
      ["Due on payday after pledging", money(t.payday_cents)]]);
    el("sale-limit").textContent = "The swap must deliver " + money(s.output_cents) + " within a limit of "
      + (s.max_lamports / 1e9).toString() + " SOL or it stops. "
      + (s.policy === "historical" && s.min_net_price_cents > q.price_cents ? "" : "That needs about "
        + money(s.min_net_price_cents) + " per SOL after swap costs. ")
      + "Network fees are separate.";
  }
  const carry = s.outcomes.carry?.histories ? s.outcomes.carry : null, outcomes = carry || s.outcomes;
  const crossover = carry ? carry.crossover_cents : s.crossover_cents;
  if (!split) el("sale-crossover").textContent = crossover == null ? "The latest share change gives no positive break-even price." :
    "At the full sale limit, about " + money(crossover) + " per SOL on payday makes selling and pledging come out even"
    + (carry ? " if the latest 15-day collateral earnings repeat." : " before collateral earnings.") + " Selling fewer coins raises that price.";
  if (split) el("sale-crossover").textContent = "At the swap limit, the split’s lowest payday result across the tested prices is "
    + (split.worst_difference_cents >= 0 ? money(split.worst_difference_cents) + " ahead of" : money(-split.worst_difference_cents) + " behind")
    + " pledging. Prices outside those cases can change the result.";
  const stress = outcomes.stress_difference_cents, histories = outcomes.histories;
  const stressLine = el("sale-stress");
  stressLine.hidden = stress == null;
  if (stress != null) {
    const price = q.price_cents * (1 - q.downside.drop_bps / 10000);
    stressLine.textContent = "At about " + money(price) + " per SOL on payday, "
      + (stress >= 0 ? "selling leaves at least " + money(stress) + " more at payday."
        : "pledging could leave up to " + money(-stress) + " more at payday.");
  }
  const ahead = histories.filter(h => h.difference_cents > 0).length;
  el("sale-histories").textContent = "Selling left at least one cent more in " + ahead + " of " + histories.length
    + " historical Gemini finishes at the selected limit. " + (s.policy === "historical" ? "These same finishes set the limit. " : "")
    + "The comparison assumes the loan survives and excludes taxes and network fees.";
  el("sale-yield").textContent = carry ? "Each finish includes the extra pledge’s change in SOL value from Kamino shares over the same 15 days. "
    + "The period ending " + carry.end + " adds " + (carry.extra_lamports / 1e9) + " SOL to the pledge comparison."
    : "Kamino share history is unavailable. These figures exclude earnings on the extra pledge.";
  el("sale-yield-link").hidden = !carry;
  el("sale-proof").hidden = !data;
  const link = el("sale-evidence");
  el("sale-live").textContent = split ? "Check the split payment on Devnet" : "Check sale and repayment on Devnet";
  link.hidden = !active.evidence;
  if (active.evidence) link.href = active.evidence;
  else link.removeAttribute("href");
  if (panel.open && !link.hidden && !el("sale-receipt").textContent) checkSale(false);
}

async function checkSale(live) {
  if (!saleRequest) return;
  const id = ++saleRevision, button = el("sale-live"), status = el("sale-receipt");
  button.disabled = live;
  status.textContent = live ? "Checking the capped swap and loan on public Devnet…" : "Reading the saved sale and repayment…";
  try {
    const p = await request("/api/midtrip-payment/preview", {...saleRequest, source: live ? "live" : "recorded"});
    if (id !== saleRevision) return;
    if ((p.added_lamports || 0) !== (saleRequest.added_lamports || 0) || p.sale_cap !== saleRequest.sale_cap || p.sale_raw !== saleRequest.output_cents * 100
        || p.payment_raw !== saleRequest.payment_cents * 100 || p.sold_lamports > p.sale_cap) throw new Error();
    status.textContent = (live ? "Public" : "Saved") + " Devnet simulation at slot " + p.slot + ". The borrower sold "
      + (p.sold_lamports / 1e9) + " test SOL for " + (p.sale_raw / 1e6).toFixed(4) + " test USDC"
      + (p.added_lamports ? " and added " + (p.added_lamports / 1e9) + " test SOL to the same pledge" : "") + ". The loan has "
      + (p.remaining_raw / 1e6).toFixed(4) + " test USDC left. The vendor received " + (p.vendor_raw / 1e6).toFixed(4)
      + ". A second check at slot " + p.rollback_slot + " rejected a one-lamport sale limit and rolled back the bundle. "
      + "These immediate checks use sponsored test funds at 1:100 scale. No funds moved.";
  } catch {
    if (id === saleRevision) status.textContent = "Sale check unavailable. The modeled comparison is still available.";
  } finally { if (id === saleRevision) button.disabled = false; }
}
el("midtrip-sale").ontoggle = () => {
  if (el("midtrip-sale").open && !el("sale-evidence").hidden && !el("sale-receipt").textContent) checkSale(false);
};
el("sale-live").onclick = () => checkSale(true);

function showPledge(q) {
  const t = q.topup, table = el("pledge-compare");
  table.hidden = el("pledge-tradeoff").hidden = false;
  el("midtrip-amounts").hidden = el("downside-cost").hidden = true;
  const values = [[money(q.payment_cents), money(t.payment_cents)], [money(q.unused_cents), money(t.unused_cents)],
    ["0 SOL", pledgedSol(t.deposit_limit_lamports)], [money(q.payday_cents), money(t.payday_cents)]];
  Array.from(table.tBodies[0].rows).forEach((row, i) => {
    row.cells[1].textContent = values[i][0];
    row.cells[2].textContent = values[i][1];
  });
  el("midtrip-verdict").textContent = t.short_cents ? "Even with extra SOL, the tested cushion needs " + money(t.short_cents) + " more cash."
    : t.deposit_limit_lamports ? "Keep " + money(t.unused_cents) + " for the road. Pledge up to " + pledgedSol(t.deposit_limit_lamports)
      + (t.payment_cents ? " and pay " + money(t.payment_cents) + " on day 15." : " with no early cash payment.")
     : t.max_lots ? "No extra SOL needs to be pledged for this plan." : "No spare SOL is available under these limits.";
  el("pledge-tradeoff").textContent = (t.deposit_limit_lamports ? "The added SOL is worth about "
    + money(Math.round(t.deposit_limit_lamports * q.price_cents / 1e9)) + " at the assumed price. It stays locked and can be liquidated. " : "")
    + (t.max_lots * t.lot_lamports < t.cap_lamports ? "The funding wallet and reserve terms limit how much more SOL can be used. " : "")
    + "Network fees are separate.";
  el("midtrip-risk").textContent = "On the same tested prices, debt peaks at " + percent(q.paid_peak_bps) + " of the pledge with cash alone and "
    + percent(t.peak_bps) + " with the spare SOL option. The liquidation limit is " + percent(q.limit_bps) + ".";
  el("midtrip-trigger").textContent = "For the cash-only alternative: " + el("midtrip-trigger").textContent;
  const trials = t.later_trials;
  el("midtrip-audit").textContent = "With the spare SOL option, earlier-only sizing missed the cushion in "
    + trials.filter(r => r.missed).length + " of " + trials.length + " later cases. Cash alone missed it in "
    + q.later_trials.filter(r => r.missed).length + ". Each replay fixes the earlier cash payment and pledge before seeing the later prices. The histories can overlap.";
}

function showBudget(row, low, inputs) {
  const o = budgetQuote(row);
  showEarlyPayment(o?.midtrip, row, inputs);
  el("exit-amounts").hidden = el("exit-how").hidden = el("exit-price").hidden = !o;
  el("cash-exit-quarter").hidden = !o || o.three_quarter_cash_cents <= o.budget_cents;
  el("exit-retained").hidden = !o;
  el("exit-budget-prices").hidden = true;
  if (!o) return;
  const retained = Math.floor(100 * o.min_left_lamports / row.pledge_lamports);
  el("exit-retained-label").textContent = "Keep at least " + retained + "% of the " + (row.pledge_lamports / 1e9) + " SOL pledged"
    + (o.short_cents ? " if you add the missing cash." : ".");
  el("exit-retained-bar").value = retained;
  el("cash-exit-quarter").textContent = "Try " + money(o.three_quarter_cash_cents) + " to keep three quarters";
  const result = o.short_cents ? ["More cash needed", money(o.short_cents)] : ["Cash you’d use", money(o.cash_required_cents)];
  facts(el("exit-amounts"), [result, [o.short_cents ? "SOL kept with extra cash" : "SOL you keep, at least", minSol(o.min_left_lamports)]]);
  el("exit-how").textContent = (o.short_cents ? "Your budget is short. The closest available plan needs " + money(o.cash_required_cents) + " in cash. " : "")
    + (o.credit_cents ? money(row.repay_cents) + " is due on day 30. Selling up to " + maxSol(o.max_lamports)
      + " covers the remaining " + money(o.credit_cents) + " at the recorded prices." : "This plan covers the estimated payoff in cash.")
    + (o.unused_cents ? " That leaves " + money(o.unused_cents) + " of your budget unused." : "") + " Network costs are separate.";
  const threshold = o.price_for_three_quarters_cents;
  el("exit-price").hidden = !o.credit_cents && !o.short_cents;
  const lowFloor = row.payoff_options.find(option => option.keep_pct === 75).low_price_cash_floor_cents;
  el("exit-price").textContent = "At the lowest tested price (" + money(low) + " per SOL), keeping three quarters would need at least "
    + money(lowFloor) + " of cash before sale costs.";
  el("exit-budget-prices").hidden = el("exit-price").hidden;
  el("exit-budget-prices").textContent = threshold == null ? "Keeping three quarters of the collateral with this budget is outside the available credit or collateral limits."
    : "To keep three quarters with this budget, the sale must net at least " + money(threshold) + " per SOL. "
      + o.prices_below + " of " + o.price_count + " tested SOL prices fall below that level before swap costs. The cases use recorded Gemini settlement moves, the opening price and your additional price.";
}

function selection(row) {
  return row.payoff_options.find(o => o.keep_pct === Number(el("exit-keep").value));
}

export function showExit(rows, low, inputs) {
  if (pending) clearExit();
  let stages = rows.filter(([, r]) => r.payoff_options);
  const check = el("payoff-check"), stage = el("exit-stage");
  const jump = el("jump-payoff");
  check.hidden = jump.hidden = !stages.length;
  if (!stages.length) return;
  const first = stages[0][1];
  if (stages.length > 1 && stages.every(([, r]) => r.draw_cents === first.draw_cents && r.repay_cents === first.repay_cents && r.pledge_lamports === first.pledge_lamports))
    stages = [["Each loan-funded stop", first]];
  jump.textContent = "Compare ways to repay " + money(first.repay_cents);
  jump.onclick = () => { check.open = true; check.querySelector("summary").focus(); };
  stage.replaceChildren(...stages.map(([name], i) => new Option(name, String(i))));
  el("exit-stage-field").hidden = stages.length < 2;
  function update() {
    const [name, row] = stages[Number(stage.value)];
    el("cash-exit-form").hidden = el("midtrip").hidden = !custom();
    el("cash-exit-status").hidden = !custom();
    el("cash-exit-check").disabled = el("midtrip-check").disabled = !!pending;
    selectProof(custom());
    if (custom()) {
      showBudget(row, low, inputs);
      return;
    }
    el("exit-amounts").hidden = el("exit-how").hidden = false;
    el("cash-exit-quarter").hidden = el("exit-retained").hidden = el("exit-budget-prices").hidden = true;
    const o = selection(row);
    facts(el("exit-amounts"), [["Cash needed for payoff", money(o.cash_cents)], ["SOL you keep, at least", minSol(o.min_left_lamports)]]);
    el("exit-how").textContent = name + " · " + money(row.repay_cents) + " estimated payoff on day 30. " + (o.credit_cents
      ? "At the recorded pool prices, selling up to " + maxSol(o.max_sol_lamports) + " raises " + money(o.sale_cents) + " toward closing the loan."
      : "Paying this amount in USDC releases the collateral in full.") + " Network costs are separate.";
    el("exit-price").hidden = o.keep_pct === 100;
    el("exit-price").textContent = "At the lowest tested price of " + money(low) + " per SOL, "
      + (o.low_price_cash_floor_cents ? "you would need at least " + money(o.low_price_cash_floor_cents) + " in cash, even before sale costs."
        : "the permitted collateral covers the payoff on paper. The actual sale still depends on available liquidity.");
  }
  async function checkBudget(event) {
    const restorePolicy = ["sale-policy", "sale-minimum", "sale-apply"].includes(event?.currentTarget?.id);
    if (!cashInput.reportValidity()) return;
    if (el("midtrip").open && !checkpoint.reportValidity()) return;
    const cents = budgetCents();
    const params = {...inputs, payoff_cash: cents};
    if (checkpoint.validity.valid) {
      params.midtrip_price = Math.round(checkpoint.valueAsNumber * 100);
      params.sale_policy = el("sale-policy").value;
      if (params.sale_policy === "budget") params.sale_loss_cents = Number(el("sale-loss").dataset.applied || 100);
      params.midtrip_topup_lamports = Number(el("midtrip-topup").value);
      params.midtrip_drop_bps = Number(el("midtrip-drop").value);
    }
    clearExit();
    const id = budgetRevision, controller = new AbortController();
    pending = controller;
    const timer = setTimeout(() => controller.abort(), 45000);
    const status = el("cash-exit-status");
    status.textContent = "Pricing the cash and collateral split…";
    update();
    try {
      const q = await request("/api/fuel-desk", params, controller.signal);
      if (id !== budgetRevision) return;
      const candidates = Object.values(q.series || q.mainnet.secured_loan.combined).filter(r => r?.cash_payoff);
      const priced = new Map(candidates.map(r => [loanKey(r), r.cash_payoff]));
      if (stages.some(([, r]) => !priced.has(loanKey(r)))) throw new Error("Missing payoff quote");
      budgetResult = {cents, rows: priced};
      status.textContent = "";
    } catch {
      if (id !== budgetRevision) return;
      status.textContent = "The budget could not be priced. Check again to retry.";
    } finally {
      clearTimeout(timer);
      if (id === budgetRevision) {
        pending = null;
        update();
        if (budgetResult && focusMidtrip) {
          focusMidtrip = false;
          const focus = ["sale", "sale-limit", "sale-replay", "split-pledge"].includes(initialView) && !el("midtrip-sale").hidden ? el("sale-title") : el("midtrip").querySelector("summary");
          focus.focus();
        } else if (budgetResult && restorePolicy && document.activeElement === document.body && !el("midtrip-sale").hidden) {
          el("sale-policy").focus();
        }
        if (!budgetResult) el("midtrip-verdict").textContent = "The price check is unavailable. Check early payment to retry.";
      }
    }
  }
  el("cash-exit-quarter").onclick = () => {
    const q = budgetQuote(stages[Number(stage.value)][1]);
    if (!q) return;
    cashInput.value = (q.three_quarter_cash_cents / 100).toFixed(2);
    checkBudget();
    cashInput.focus();
  };
  checkpoint.oninput = cashInput.oninput = () => {
    clearExit();
    el("cash-exit-status").textContent = "Check your budget to see the updated payoff.";
    update();
  };
  checkpoint.onkeydown = cashInput.onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); checkBudget(); } };
  el("sale-apply").onclick = event => {
    const input = el("sale-loss");
    if (!input.reportValidity()) return;
    input.dataset.applied = String(Math.round(input.valueAsNumber * 100));
    checkBudget(event);
  };
  el("sale-loss").onkeydown = event => {
    if (event.key === "Enter") { event.preventDefault(); el("sale-apply").click(); }
  };
  el("sale-minimum").onclick = event => {
    const cents = el("sale-minimum").dataset.cents;
    el("sale-policy").value = "budget";
    el("sale-loss").value = (Number(cents) / 100).toFixed(2);
    el("sale-loss").dataset.applied = cents;
    checkBudget(event);
  };
  el("sale-policy").onchange = el("midtrip-topup").onchange = el("midtrip-drop").onchange = checkBudget;
  el("midtrip-check").onclick = el("cash-exit-check").onclick = checkBudget;
  stage.onchange = update;
  el("exit-keep").onchange = () => {
    clearExit();
    el("cash-exit-status").textContent = "Enter your available cash and check the payoff.";
    update();
  };
  update();
  if (jumpToPayoff) {
    jumpToPayoff = false;
    check.open = true;
    check.querySelector("summary").focus();
    if (custom()) checkBudget();
  }
}

export function exitLines(row) {
  if (!row.payoff_options) return [];
  if (custom()) {
    const o = budgetQuote(row);
    if (!o) return ["Custom cash-budget payoff has no current quote."];
    return ["Optional payoff using a " + money(o.budget_cents) + " cash budget. Estimated cash required: " + money(o.cash_required_cents)
      + ". Additional cash needed: " + money(o.short_cents) + ". Unused budget: " + money(o.unused_cents),
      "Temporary USDC: " + money(o.credit_cents) + ". Fee allowance: " + o.fee_cap_raw + " micro-USDC. Maximum SOL sold: " + o.max_lamports
      + " lamports. Minimum collateral left: " + o.min_left_lamports + " lamports.",
      ...(el("midtrip").open && o.midtrip ? [earlyPaymentLine(o.midtrip)] : []),
      ...o.legs.map(l => "Orca " + l.pool + " · exact output " + l.output_raw + " micro-USDC."),
      "Recorded prices. Network costs are separate. Reprice before the next stop."];
  }
  const o = selection(row);
  return ["Optional single-stop payoff at recorded prices. Keep at least " + o.keep_pct + "% of pledged SOL.",
    "Driver USDC needed: " + money(o.cash_cents) + ". Temporary credit: " + money(o.credit_cents)
      + ". Orca sale output: " + money(o.sale_cents) + ". Maximum SOL sold: " + o.max_sol_lamports + " lamports.",
    "Flash fee allowance: " + (o.fee_cap_raw / 1e6).toFixed(6) + " USDC. Sale proceeds above the payoff and fee stay in the wallet.",
    "Pledged SOL left: at least " + o.min_left_lamports + " lamports. Separate network fees and temporary account deposits apply. Reprice the next stop after using collateral."];
}

let lastProof, mixedProof, proofRevision = 0;
function selectProof(mixed) {
  if (mixedProof === mixed) return;
  mixedProof = mixed;
  proofRevision++;
  lastProof = null;
  el("exit-receipt").hidden = true;
  el("exit-simulate").disabled = false;
  el("exit-proof-result").textContent = "";
  el("exit-proof-title").textContent = mixed ? "Verify the $30 cash example on Devnet" : "Verify a payoff with zero starting USDC";
  el("exit-proof-scope").textContent = "This separate public Devnet example opens and immediately closes the sample $72.98 loan at 1:100 scale. "
    + (mixed ? "The sponsor buys and supplies 0.30 test USDC to represent the driver’s cash contribution." : "A sponsor supplies test SOL for the opening, accounts and network fee.");
  el("exit-evidence").href = "/evidence/" + (mixed ? "cash-exit" : "collateral-exit");
  if (el("exit-proof").open) showProof(false);
}
async function showProof(live) {
  const status = el("exit-proof-result"), button = el("exit-simulate");
  const id = ++proofRevision;
  button.disabled = true;
  status.textContent = live ? "Checking payoff and sale limits on public Devnet…" : "Reading the saved Devnet payoff…";
  try {
    const p = await request((mixedProof ? "/api/cash-exit" : "/api/collateral-exit") + (live ? "/simulate" : ""));
    if (id !== proofRevision) return;
    facts(el("exit-receipt"), [...(mixedProof ? [["Cash contribution", (p.cash_contributed_raw / 1e6).toFixed(6) + " test USDC"]] : []), ["Fuel debt repaid", (p.repaid_raw / 1e6).toFixed(6) + " test USDC"],
      ["Released SOL sold", (p.sold_lamports / 1e9).toFixed(9)], ["Released SOL kept", (p.retained_lamports / 1e9).toFixed(9)]]);
    el("exit-receipt").hidden = false;
    lastProof = (live ? "Public Devnet simulation" : "Saved Devnet simulation") + " · slot " + p.slot
      + ". " + (mixedProof ? "Temporary credit covered the balance after the sponsored contribution. " : "The borrower started with zero USDC. ") + "The vendor received " + (p.vendor_raw / 1e6).toFixed(4)
      + " test USDC and both debts closed. A second simulation at slot " + p.rollback_slot
      + " rejected a one-lamport sale limit and rolled back the bundle. No transaction was submitted.";
    status.textContent = lastProof;
  } catch {
    if (id !== proofRevision) return;
    status.textContent = "Payoff check unavailable. " + (lastProof || "The saved evidence is available to download.");
  } finally { if (id === proofRevision) button.disabled = false; }
}
el("exit-proof").ontoggle = () => {
  if (el("exit-proof").open && !lastProof && !el("exit-simulate").disabled) showProof(false);
};
el("exit-simulate").onclick = () => showProof(true);

function earlyPaymentLine(q) {
  const begin = "Alternative if cash arrives on day 15 at " + money(q.price_cents) + "/SOL. ";
  const thresholds = " Minimum day-15 SOL price for the tested cushion: " + money(q.no_payment_price_cents)
    + " with zero payment or " + money(q.budget_price_cents) + " with this budget, assuming an intact pledge.";
  const downside = q.downside ? " Additional " + percent(q.downside.drop_bps) + " fall to " + money(q.downside.floor_cents) + "/SOL." : "";
  if (!q.eligible) return begin + downside + "Loan may already be eligible for liquidation." + thresholds;
  const t = q.topup;
  const spare = t?.eligible ? " Spare SOL alternative: pledge up to " + t.deposit_limit_lamports + " lamports and pay "
    + money(t.payment_cents) + " on day 15. Cash left: " + money(t.unused_cents) + ". Due day 30: " + money(t.payday_cents)
    + ". Missing cash for the tested cushion: " + money(t.short_cents) + ". Network fees are separate." : "";
  const sale = t?.sale;
  const sell = sale && !sale.max_lamports ? " Historical sale limit unavailable. Recheck the pledge option." : sale ? " Sell instead for the same road cash: receive " + money(sale.output_cents) + " using at most "
    + sale.max_lamports + " lamports, add " + money(sale.cash_cents) + " of cash, and owe " + money(sale.payday_cents)
    + " on day 30. The swap must fill within the cap. Network fees are separate."
    + (sale.policy === "budget" ? " Applied payday downside limit: " + money(sale.loss_limit_cents) + ". " : "")
    + (sale.modeled_fill ? " The modeled Orca input fits." : " The modeled Orca sale does not fit.")
    + (sale.policy === "historical" ? " This limit requires at least one cent over pledging in each matched historical finish, including collateral earnings." : "")
    : "";
  const split = sale?.split;
  const mixed = split ? " Smaller sale and pledge: receive " + money(split.output_cents) + " within " + split.max_lamports
    + " lamports, pledge " + split.deposit_limit_lamports + " lamports and add " + money(split.cash_cents) + " cash. Road cash left: "
    + money(split.unused_cents) + ". Due on payday: " + money(split.payday_cents) + "." : "";
  return begin + "Pay " + money(q.payment_cents) + " early, keep " + money(q.unused_cents) + " cash, still owe "
    + money(q.payday_cents) + " on day 30. Additional cash needed for the tested cushion: " + money(q.short_cents)
    + ". Worst modeled debt as a share of collateral: " + percent(q.paid_peak_bps) + " across " + q.windows + " Gemini paths" + (q.downside ? " plus driver stress." : ".") + downside + thresholds
    + (q.downside ? " History alone requires " + money(q.downside.history_needed_cents) + " early. Minimum price through payday with available cash: " + money(q.cash_floor_cents) + "/SOL." : "")
    + " Earlier-only sizing missed the cushion in " + q.later_trials.filter(t => t.missed).length + " of " + q.later_trials.length + " eligible later cases." + spare + sell + mixed;
}

function partialSummary(q, live) {
  const units = n => (n / 1e6).toFixed(4);
  return (live ? "Public" : "Saved") + " Devnet simulation at slot " + q.slot + ". The vendor received "
      + units(q.vendor_raw) + " test USDC. Paying " + units(q.payment_raw) + " left " + units(q.remaining_raw)
      + " of debt and all " + (q.pledge_lamports / 1e9) + " test SOL pledged. No transaction was submitted.";
}

let partialReceipt = "";
async function showPartialReceipt(live) {
  const receipt = el("midtrip-receipt"), button = el("midtrip-live");
  button.disabled = true;
  receipt.textContent = live ? "Checking the open loan on Devnet…" : "Reading the saved simulation…";
  try {
    const q = await request("/api/midtrip-payment" + (live ? "/simulate" : ""));
    partialReceipt = partialSummary(q, live);
    receipt.textContent = partialReceipt;
  } catch {
    receipt.textContent = "Check unavailable. " + (partialReceipt || "You can download the saved evidence.");
  } finally { button.disabled = false; }
}
el("midtrip-proof").ontoggle = () => {
  if (el("midtrip-proof").open && !partialReceipt && !el("midtrip-live").disabled) showPartialReceipt(false);
};
el("midtrip-live").onclick = () => showPartialReceipt(true);

let downsideProof, proofKey = "", lastDownside = "";

function setDownsideProof(q, row, inputs) {
  const t = q?.topup, d = t?.preview ? t : q?.downside;
  const panel = el("downside-proof");
  const requestData = t?.preview ? {plan: {...inputs, payoff_cash: budgetCents(), midtrip_price: q.price_cents,
    midtrip_drop_bps: q.downside?.drop_bps || 0, midtrip_topup_lamports: t.cap_lamports},
    opening_lamports: row.opening_lamports, added_lamports: t.preview.added_lamports, payment_cents: t.payment_cents}
    : d?.preview_raw ? {price_cents: q.price_cents, budget_cents: budgetCents(), drop_bps: d.drop_bps, payment_cents: q.payment_cents} : null;
  const key = JSON.stringify(requestData);
  el("midtrip-proof").hidden = !!requestData;
  panel.hidden = !requestData;
  if (key === proofKey) return;
  proofKey = key;
  downsideProof = requestData;
  lastDownside = "";
  el("downside-live").disabled = false;
  el("downside-receipt").textContent = "";
  if (!requestData) return;
  el("downside-proof-title").textContent = t?.preview ? "Verify the extra pledge on Devnet" : "Verify the " + money(q.payment_cents) + " early payment";
  const link = el("downside-evidence");
  link.hidden = !d.evidence;
  if (link.hidden) {
    link.removeAttribute("href");
    el("downside-receipt").textContent = "Check the selected amount against the open loan on public Devnet.";
  } else {
    link.href = d.evidence;
    if (panel.open) checkDownside(false);
  }
}

async function checkDownside(live) {
  if (!downsideProof) return;
  const current = downsideProof, status = el("downside-receipt"), button = el("downside-live");
  button.disabled = true;
  status.textContent = "Verifying the selected partial payment…";
  try {
    const p = await request("/api/midtrip-payment/preview", {...current, source: live ? "live" : "recorded"});
    if (current !== downsideProof) return;
    if (p.payment_raw !== current.payment_cents * 100 || (p.added_lamports || 0) !== (current.added_lamports || 0)) throw new Error("Payment differs from quote");
    lastDownside = p.added_lamports ? (live ? "Public" : "Saved") + " Devnet simulation at slot " + p.slot
      + ". The borrower added " + (p.added_lamports / 1e9) + " test SOL and paid " + (p.payment_raw / 1e6).toFixed(4)
      + " test USDC. Kamino records " + (p.pledge_lamports / 1e9) + " test SOL pledged and " + (p.remaining_raw / 1e6).toFixed(4)
      + " test USDC owed. The vendor received " + (p.vendor_raw / 1e6).toFixed(4) + ". No transaction was submitted." : partialSummary(p, live);
    status.textContent = lastDownside;
  } catch {
    if (current === downsideProof) status.textContent = "Check unavailable. " + (lastDownside || "The modeled plan is still available.");
  } finally { if (current === downsideProof) button.disabled = false; }
}

el("downside-proof").ontoggle = () => {
  if (el("downside-proof").open && !el("downside-evidence").hidden && !lastDownside && !el("downside-live").disabled) checkDownside(false);
};
el("downside-live").onclick = () => checkDownside(true);
