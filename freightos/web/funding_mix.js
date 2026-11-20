import {el, facts, money, saveFile} from "./common.js";

export function pathVerdict(check, loan) {
  const cents = (check.peak_ltv_bps / 100).toLocaleString("en-US", {maximumFractionDigits: 2});
  return "The closest call came " + (check.hours / 24) + " days into the replay starting " + check.start
    + ". At " + money(check.price_cents) + " per SOL, the debt reached " + cents
    + "¢ per dollar pledged. Kamino’s forced-sale threshold was " + loan.liquidation_pct + "¢.";
}

function showPath(check, loan) {
  el("mix-path").hidden = !check;
  if (!check) return;
  el("path-verdict").textContent = pathVerdict(check, loan);
  el("path-limit").textContent = loan.liquidation_pct + "¢ forced-sale threshold";
  const group = el("path-dots");
  group.replaceChildren(...check.samples.map(mark => {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", 3 + 354 * mark.hours / 720);
    dot.setAttribute("cy", 85 - 85 * mark.ltv_bps / (loan.liquidation_pct * 100));
    dot.setAttribute("r", mark.hours === check.hours ? 3.2 : 1.8);
    return dot;
  }));
  const c = loan.path_coverage;
  el("path-scope").textContent = "Each dot applies a Gemini price move to the starting collateral price. We checked "
    + c.observations + " distinct settlements across " + c.windows + " overlapping 30-day periods. " + c.omitted
    + " scheduled observations were excluded. Prices can fall between checks, up to " + c.max_gap_hours + " hours apart in this sample.";
  el("path-event").href = "https://api.gemini.com/v1/prediction-markets/events/" + encodeURIComponent(check.ticker);
}

export function mixKey() {
  const priority = el("mix-priority").value;
  if (el("payday-allow").checked) return priority;
  return priority === "plan" ? "without_loan" : priority + "_without_loan";
}

function paydayBalance(cents) {
  if (cents === undefined) return "Fuel short";
  return cents < 0 ? money(-cents) + " short" : money(cents);
}

function showPaydayPrices(options, show) {
  const panel = el("payday-prices");
  panel.hidden = !show || !options.prices || !options.most_cash?.draw_cents;
  if (panel.hidden) return;
  el("payday-price-rows").replaceChildren(...options.prices.map(price => {
    const tr = document.createElement("tr");
    for (const text of [money(price.price_cents), paydayBalance(price.allowed_cents), paydayBalance(price.debt_free_cents)]) {
      const td = tr.insertCell(); td.textContent = text;
    }
    return tr;
  }));
  const limits = options.switch_prices, note = el("payday-switch");
  note.hidden = !limits;
  if (limits) {
    note.textContent = "If the loan stays open until payday, borrowing leaves more at " + money(limits.borrowing_from) + "/SOL or higher."
      + (limits.debt_free_through === null ? "" : " The same debt-free plan leaves more at " + money(limits.debt_free_through) + "/SOL or lower.")
      + (limits.debt_free_through === null ? "" : " Cent rounding matters between the two prices.");
  }
  const h = options.history;
  el("payday-history").textContent = "Both plans keep their original funding split. Across " + h.windows
    + " overlapping Gemini 30-day moves, borrowing left more in " + h.ahead + " cases and tied in " + h.ties + ".";
}

function showPaydayChoice(q, options, row) {
  const gap = q.floor_cents - row.payday_cents;
  el("mix-preview").textContent = paydayBalance(row.payday_cents) + (row.payday_cents < 0 ? "" : " on payday");
  let action = "Cover fuel without a new loan.";
  if (row.advance_cents) action = "Request " + money(row.advance_cents) + " before the stop.";
  else if (row.draw_cents) action = "Borrow " + money(row.draw_cents) + " for fuel.";
  el("mix-title").textContent = gap > 0 ? money(gap) + " below your payday target." : action;
  const other = options.most_cash_without_loan, best = options.most_cash;
  let tradeoff = "Only the borrowing option can fund the whole stop under these terms.";
  if (other) {
    const advantage = best.payday_cents - other.payday_cents;
    tradeoff = advantage ? "At " + money(q.high_price_cents) + " per SOL, borrowing adds " + money(advantage) + " to the best debt-free payday balance and locks "
      + (best.pledge_lamports / 1e9).toFixed(4) + " SOL until repayment." : "The highest balance is available without a loan.";
  }
  el("mix-reason").textContent = (gap > 0 ? "Fuel clears, but even this choice misses the " + money(q.floor_cents) + " target. " : "")
    + tradeoff;
}

export function showMix(q) {
  const options = q.mainnet?.secured_loan?.combined;
  el("mix").hidden = !options;
  if (!options) return;
  const priority = el("mix-priority").value;
  const payday = priority === "most_cash", keepSol = priority === "keep_sol";
  const credit = options[priority], debtFree = options[priority === "plan" ? "without_loan" : priority + "_without_loan"];
  el("funding-comparison-label").textContent = payday ? "Lowest cash left across tested prices"
    : keepSol ? "Value of SOL still available after funding" : "Customer cash needed before the stop";
  function figure(row, bestPayday) {
    if (payday) return paydayBalance(row?.payday_cents);
    if (keepSol && !row) return bestPayday ? "Target missed" : "Fuel short";
    return row ? money(keepSol ? row.available_sol_value_cents : row.advance_cents) : "Outside limits";
  }
  el("payday-with").textContent = figure(credit, options.most_cash);
  el("payday-without").textContent = figure(debtFree, options.most_cash_without_loan);
  const key = mixKey(), r = options[key];
  el("mix-figures").hidden = !r;
  showPath(r?.path_check, q.mainnet.secured_loan);
  showPaydayPrices(options, payday);
  el("mix-request").hidden = !r?.advance_cents || r.payday_cents < q.floor_cents;
  if (!r) {
    const bestPayday = el("payday-allow").checked ? options.most_cash : options.most_cash_without_loan;
    el("mix-preview").textContent = "Outside these terms";
    el("mix-title").textContent = "This choice falls short";
    el("mix-reason").textContent = payday ? "These sources cannot cover the full fuel bill within your wallet and cash limits."
      : keepSol && bestPayday ? "Fuel clears. The best payday outcome with this choice is " + paydayBalance(bestPayday.payday_cents)
        + ", which misses your target by " + money(q.floor_cents - bestPayday.payday_cents) + "."
      : !el("payday-allow").checked && credit
      ? "With your current limits, covering fuel and keeping " + money(q.floor_cents) + " on payday takes a loan. Choose Borrowing allowed to see that plan."
      : "The available early payment, cash and wallet budget cannot cover fuel and keep " + money(q.floor_cents) + " at the highest tested SOL price.";
    return;
  }
  if (payday) {
    showPaydayChoice(q, options, r);
  } else if (keepSol) {
    const bestPayday = el("payday-allow").checked ? options.most_cash : options.most_cash_without_loan;
    const extra = r.available_sol_value_cents - bestPayday.available_sol_value_cents;
    const tradeoff = bestPayday.payday_cents - r.payday_cents;
    el("mix-preview").textContent = money(r.available_sol_value_cents) + " in SOL available";
    const actions = [];
    if (r.draw_cents) actions.push("borrow " + money(r.draw_cents));
    if (r.sale_cents) actions.push("sell SOL for " + money(r.sale_cents));
    const action = actions.join(" and ");
    el("mix-title").textContent = action ? action[0].toUpperCase() + action.slice(1) + "." : "Cover fuel without a loan or sale.";
    el("mix-reason").textContent = "You asked for " + money(q.floor_cents) + " on payday. This plan leaves " + money(r.payday_cents)
      + " at " + money(q.high_price_cents) + " per SOL. " + (extra || tradeoff ? "Compared with maximizing payday cash, this leaves "
        + money(extra) + " more in SOL available today for " + money(tradeoff) + " less in the lowest payday outcome."
        : "This also matches the best payday balance under these terms.");
  } else {
    el("mix-preview").textContent = r.advance_cents ? money(r.advance_cents) + " early" : "No early pay needed";
    el("mix-title").textContent = r.advance_cents ? "Ask for " + money(r.advance_cents) + " early." : "Keep your invoice on regular terms.";
    const alternative = key === "plan" ? options.without_loan : options.plan;
    const difference = alternative ? Math.abs(alternative.advance_cents - r.advance_cents) : 0;
    el("mix-reason").textContent = difference ? (key === "plan"
      ? "Borrowing " + money(r.draw_cents) + " lets you ask the customer for " + money(difference) + " less."
      : "You ask the customer for " + money(difference) + " more and avoid the " + money(alternative.draw_cents) + " loan.")
      : r.draw_cents ? "This is the smallest loan that works with the smallest available customer payment under your limits."
      : "Your cash and available funds cover the stop without borrowing.";
  }
  const sources = [["Cash you bring", r.cash_cents], ["Customer pays before the stop", r.advance_cents - r.surplus_cents],
    ["Wallet dollars and fuel reserve", r.stable_cents], ["Sell SOL for", r.sale_cents], ["Borrow against SOL", r.draw_cents]];
  facts(el("mix-sources"), [...sources.filter(([, cents]) => cents > 0).map(([name, cents]) => [name, money(cents)]),
    ["Planned fuel payment", money(q.bill_cents)]]);
  el("mix-payday").textContent = (r.advance_cents ? "The customer keeps " + money(r.fee_cents) + " and owes " + money(r.invoice_balance_cents) + " later. " : "")
    + (r.surplus_cents ? money(r.surplus_cents) + " of the advance stays with you. " : "")
    + (r.draw_cents ? "Estimated loan payoff is " + money(r.repay_cents) + " on day 30. " : "")
    + "After restoring your balances at " + money(q.high_price_cents) + " per SOL, "
    + (r.payday_cents >= 0 ? "you keep " + money(r.payday_cents) : "you are " + money(-r.payday_cents) + " short") + ".";
  el("mix-risk").textContent = (r.draw_cents ? (r.pledge_lamports / 1e9).toFixed(4) + " SOL stays locked until repayment. Kamino can sell it if its value falls too far. " : "")
    + (r.advance_cents ? "Confirm the amount and date with the customer before relying on it. " : "");
  el("mix-risk").hidden = !r.draw_cents && !r.advance_cents;
  el("mix-assumptions").textContent = "We use your available cash first. Orca pool prices tell us what a sale would cost. Kamino supplies the borrowing terms. Settled Gemini prediction markets and the extra SOL price set the payday range."
    + (r.draw_cents ? " The 30-day payoff assumes an existing lending account and twice the recorded variable rate. We include borrowing and repayment fee allowances." : "")
    + (keepSol ? " We keep as much of your SOL budget available as possible after sales, fees, account rent and collateral, while meeting your payday target. Its dollar value uses the recorded Kamino price. It remains SOL." : "")
    + (payday ? " We maximize the smallest payday balance across tested prices. Ties favor less debt, then a smaller advance." : "")
    + " This is a mainnet estimate. Reviewing it moves no funds.";
}

export function downloadOffer(row, reference = "FreightOS sample load FOS-2048") {
  if (!row?.advance_cents || row.fee_cents === null) return;
  const draft = [
    "Draft early-payment request" + (reference ? " · " + reference : ""), "",
    "Could you send " + money(row.advance_cents) + " before the fuel stop on this load?",
    "With an early-payment discount of " + money(row.fee_cents) + ", that would settle "
      + money(row.advance_cents + row.fee_cents) + " of freight charges and leave " + money(row.invoice_balance_cents) + " on regular terms.",
    "Please confirm the amount, discount and arrival time before we rely on the payment.", ""
  ];
  saveFile(reference === "FreightOS sample load FOS-2048" ? "FOS-2048-early-payment-request.txt" : "FreightOS-early-payment-request.txt", draft.join("\n"), "text/plain;charset=utf-8");
}
