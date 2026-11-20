import {el, facts, money, request} from "./common.js";

const minSol = raw => (Math.floor(raw / 1000) / 1e6).toFixed(6) + " SOL";
const maxSol = raw => (Math.ceil(raw / 1000) / 1e6).toFixed(6) + " SOL";
const initialView = new URLSearchParams(location.search).get("view");
let jumpToPayoff = ["payoff", "cash-budget", "midtrip", "downside"].includes(initialView);
const openMidtrip = ["midtrip", "downside"].includes(initialView);
if (initialView === "downside") el("midtrip-drop").value = "3000";
if (openMidtrip) el("midtrip").open = true;
if (["midtrip", "downside", "cash-budget"].includes(initialView)) el("exit-keep").value = "cash";

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

function showEarlyPayment(q) {
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
  setDownsideProof(q);
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
}

function showBudget(row, low) {
  const o = budgetQuote(row);
  showEarlyPayment(o?.midtrip);
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
      showBudget(row, low);
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
  async function checkBudget() {
    if (!cashInput.reportValidity()) return;
    if (el("midtrip").open && !checkpoint.reportValidity()) return;
    const cents = budgetCents();
    const params = {...inputs, payoff_cash: cents};
    if (checkpoint.validity.valid) {
      params.midtrip_price = Math.round(checkpoint.valueAsNumber * 100);
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
      if (focusMidtrip) {
        focusMidtrip = false;
        el("midtrip").querySelector("summary").focus();
      }
    } catch {
      if (id !== budgetRevision) return;
      status.textContent = "The budget could not be priced. Check again to retry.";
    } finally {
      clearTimeout(timer);
      if (id === budgetRevision) {
        pending = null;
        update();
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
  el("midtrip-drop").onchange = checkBudget;
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
  return begin + "Pay " + money(q.payment_cents) + " early, keep " + money(q.unused_cents) + " cash, still owe "
    + money(q.payday_cents) + " on day 30. Additional cash needed for the tested cushion: " + money(q.short_cents)
    + ". Worst modeled debt as a share of collateral: " + percent(q.paid_peak_bps) + " across " + q.windows + " Gemini paths" + (q.downside ? " plus driver stress." : ".") + downside + thresholds
    + (q.downside ? " History alone requires " + money(q.downside.history_needed_cents) + " early. Minimum price through payday with available cash: " + money(q.cash_floor_cents) + "/SOL." : "")
    + " Earlier-only sizing missed the cushion in " + q.later_trials.filter(t => t.missed).length + " of " + q.later_trials.length + " eligible later cases.";
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

function setDownsideProof(q) {
  const d = q?.downside;
  const panel = el("downside-proof");
  const requestData = d?.preview_raw ? {price_cents: q.price_cents, budget_cents: budgetCents(), drop_bps: d.drop_bps, payment_cents: q.payment_cents} : null;
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
  el("downside-proof-title").textContent = "Verify the " + money(q.payment_cents) + " early payment";
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
    if (p.payment_raw !== current.payment_cents * 100) throw new Error("Payment differs from quote");
    lastDownside = partialSummary(p, live);
    status.textContent = lastDownside;
  } catch {
    if (current === downsideProof) status.textContent = "Check unavailable. " + (lastDownside || "The modeled plan is still available.");
  } finally { if (current === downsideProof) button.disabled = false; }
}

el("downside-proof").ontoggle = () => {
  if (el("downside-proof").open && !el("downside-evidence").hidden && !lastDownside && !el("downside-live").disabled) checkDownside(false);
};
el("downside-live").onclick = () => checkDownside(true);
