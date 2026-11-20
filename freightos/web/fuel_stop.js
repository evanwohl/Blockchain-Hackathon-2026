import {el, facts, money, request, saveFile} from "./common.js";

const form = el("stop-inputs"), verify = el("verify"), note = el("check-status");
let focusMainnet = false, focusOffer = false, keepRoadCash = false;
let originalCounter, focusLoan = false, focusMix = false;
let focusWait = false, focusRange = false;
let preference = "keep-cash";
let revision = 0, timer, pending, last, undo;

function inputs() {
  if (!form.checkValidity()) return null;
  const amounts = Object.fromEntries(["cash", "budget", "target", "received", "stress"].map(id => {
    const input = el(id);
    return [id, input.value === "" ? null : Math.round(input.valueAsNumber * 100)];
  }));
  if (el("offer-menu").value === "counter") {
    return {...amounts, offer_menu: "counter", offer_amount: Math.round(el("offer_amount").valueAsNumber * 100),
      offer_fee: Math.round(el("flat_fee").valueAsNumber * 100)};
  }
  return {...amounts, offer_fee: el("offer_fee").disabled ? 0 : Math.round(el("offer_fee").valueAsNumber * 100),
    offer_menu: el("offer-menu").value};
}

function tryAmount(id, cents) {
  const field = el(id);
  undo = {field, value: field.value};
  if (id === "received") field.closest("details").open = true;
  field.value = (cents / 100).toFixed(2);
  estimate();
}

function repairs(q) {
  const controls = el("fixes");
  controls.replaceChildren();
  function button(label, action) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.onclick = action; controls.append(b);
  }
  for (const fix of q.repairs) {
    const name = {cash: "cash", budget: "wallet limit", received: "freight by payday"}[fix.input];
    button("Try " + money(fix.amount_cents) + " " + name + " (+" + money(fix.increase_cents) + ")", () => {
      tryAmount(fix.input, fix.amount_cents); el("decision").focus({preventScroll: true});
    });
  }
  if (undo) button("Undo comparison", () => {
    undo.field.value = undo.value; undo = null; estimate(); el("decision").focus({preventScroll: true});
  });
  el("changes").hidden = !controls.childElementCount;
  el("change-heading").textContent = q.repairs.length ? "Ways to cover fuel and protect payday" : "Changed your planning limit";
}

function showPriceRoom(q) {
  const panel = el("price-room"), selected = q.selected_limit, better = q.price_first;
  panel.hidden = !selected;
  if (!selected) return;
  const limited = selected.state === "bounded", button = el("price-choice");
  const label = money(q.floor_cents) + " payday target";
  el("price-limit").textContent = limited
    ? "With " + money(q.receipt_cents) + " of freight paid and the quoted spending cap, this split keeps your " + label + " through " + money(selected.price_cents) + " per SOL. At "
      + money(selected.price_cents + 1) + " it falls short after restoring the money used for fuel."
    : selected.state === "unbounded" ? "This split needs no SOL buyback. Your " + label + " holds at any SOL price under the entered freight terms."
    : "Even a free SOL buyback leaves this split below your " + label + " under the entered freight terms.";
  let comparison = "";
  button.hidden = true;
  if (q.status === "ready" && better.cash_cents !== q.cash_first.cash_cents) {
    const cost = money(better.cash_cents - q.cash_first.cash_cents);
    comparison = "Bringing " + cost + " more of your available cash today "
      + (better.state === "unbounded" ? "removes the buyback entirely." : "raises the limit from "
        + money(q.cash_first.price_cents) + " to " + money(better.price_cents) + " per SOL.");
    button.hidden = false;
    button.textContent = q.preference === "keep-cash" ? "Compare bringing " + money(better.cash_cents)
      : "Return to bringing " + money(q.cash_first.cash_cents);
  } else if (q.status === "payday_short" && better) {
    comparison = "Across all splits within your cash and wallet limits, the highest buyback price that still leaves "
      + money(q.floor_cents) + " is " + money(better.price_cents) + " per SOL.";
  } else if (q.status === "ready" && limited) {
    comparison = "No other split within your limits covers a higher buyback price.";
  }
  el("price-comparison").textContent = comparison;
  el("price-comparison").hidden = !comparison;
  el("price-history").textContent = q.gemini_windows ? "The largest move across " + q.gemini_windows
    + " recorded 30-day Gemini windows puts SOL at " + money(q.gemini_high_cents)
    + " from the demo starting price. Future prices can go higher." : "The comparison uses the entered prices.";
}

function chosenOffer(offer) {
  return offer.menu === "counter" ? offer.counter.plan : keepRoadCash ? offer.keep_cash : offer.smallest;
}

function paydayChange(delta) {
  return delta === 0 ? "the same cash" : money(Math.abs(delta)) + (delta > 0 ? " more" : " less");
}

function showPriceCase(wait, fee) {
  const choice = el("wait-scenario");
  if (!wait[choice.value]) choice.value = "starting_price";
  for (const option of choice.options) option.disabled = !wait[option.value];
  const c = wait[choice.value], limit = c.fee_limit_cents;
  el("case-price").textContent = "Buy SOL back at " + money(c.price_cents) + " per SOL";
  el("case-early").textContent = money(c.early_cents);
  el("case-wait").textContent = money(c.wait_cents);
  el("case-fee").textContent = limit === null ? "Even free early pay would miss your target or leave less cash than waiting at this price."
    : "At this price, a flat fee up to " + money(limit) + " meets your target and leaves at least as much as waiting with this cash split.";
  el("price-fee").hidden = limit === null || limit >= fee;
  if (!el("price-fee").hidden) el("price-fee").textContent = "Reprice the offer at a " + money(limit) + " fee";
}

function showCrossover(wait) {
  const cross = wait.crossover, title = el("cross-title"), range = el("cross-range");
  if (cross.constant_difference_cents !== undefined) {
    title.textContent = "The buyback price does not change the choice";
    range.textContent = "Both plans replace the same amount of SOL. Early pay leaves " + paydayChange(cross.constant_difference_cents) + " on payday at every price.";
    return;
  }
  const first = cross.first_match_cents, ahead = cross.always_ahead_cents;
  if (first === ahead) {
    title.textContent = "Early pay leaves more from " + money(ahead) + " per SOL";
  } else if (cross.ties_only) {
    title.textContent = "Same payday cash at " + money(first) + (first === ahead - 1 ? "" : "–" + money(ahead - 1)) + " per SOL";
  } else {
    title.textContent = "Rounding affects the choice between " + money(first) + " and " + money(ahead - 1) + " per SOL";
  }
  range.textContent = (first ? "Below " + money(first) + ", waiting leaves more. " : "")
    + "From " + money(ahead) + " up, early pay leaves more with the same driver cash.";
}

function showRange(q, counter) {
  const select = el("fee-range");
  for (const option of select.options) {
    const band = counter.ranges[option.value];
    option.textContent = (option.value === "full" ? "Full range" : "Starting price and up") + " · " + money(band.low_cents) + " to " + money(band.high_cents);
  }
  const band = counter.ranges[select.value], row = band.split;
  const title = el("range-title"), note = el("range-decision");
  el("range-draft").hidden = el("range-ledger").hidden = !!band.reason;
  el("range-limit").hidden = !!band.reason;
  if (band.reason) {
    title.textContent = band.reason === "waiting" ? "Even a zero fee falls short" : "These terms cannot be compared";
    note.textContent = ({
      waiting: row && "Among splits that protect your payday target, the closest result with free early pay still leaves "
        + money(-row.worst_difference_cents) + " less than waiting at " + money(row.worst_price_cents) + " per SOL.",
      funding: "Your available cash and wallet limit must cover fuel both with the advance and while waiting, using the same driver cash.",
      payday: "Even free early pay falls below your payday target at the high price for every split that could also fund waiting.",
      invoice: "The requested early payment exceeds the freight you expect to collect by payday."
    })[band.reason];
    return;
  }
  title.textContent = row.fee_cents === counter.flat_fee_cents ? "The offered fee works" : "Ask for a " + money(row.fee_cents) + " flat fee";
  note.textContent = "Collect " + money(row.advance_cents) + " early and bring " + money(row.cash_cents)
    + ". At every cent price in this range, this plan meets your " + money(q.floor_cents) + " target and leaves at least as much as waiting with the same cash.";
  el("range-draft").textContent = "Download request with " + money(row.fee_cents) + " fee";
  facts(el("range-ledger"), [[row.sale_cents ? "Wallet pays, including " + money(row.sale_cents) + " from selling SOL" : "Wallet pays for fuel", money(row.wallet_cents)], ["Flat discount", money(row.fee_cents)],
    ["Freight left to collect by payday", money(row.later_cents)], ["Left at " + money(band.high_cents) + " per SOL", money(row.payday_cents)]]);
  el("range-limit").textContent = "Across all affordable splits, the highest fee that works is " + money(band.best_limit_cents) + "."
    + (band.low_cents > counter.ranges.full.low_cents ? " This range excludes a drop below the starting price." : "");
}

function showCounter(q, c) {
  const row = c.plan;
  el("offer-switch").hidden = el("offer-tradeoff").hidden = el("offer-fuel-only").hidden = true;
  el("offer-preview").textContent = money(c.net_cents) + " early";
  let verdict;
  if (row) {
    verdict = "Bring " + money(row.cash_cents) + ". The wallet could pay " + money(row.wallet_cents) + " and meet your payday target.";
    if (c.surplus_cents) verdict += " Keep the " + money(c.surplus_cents) + " of unused customer money through payday.";
  } else if (c.receipt_gap_cents) {
    verdict = "The payment and discount exceed freight expected by payday by " + money(c.receipt_gap_cents) + ".";
  } else if (c.fuel_gap_cents) {
    verdict = money(c.fuel_gap_cents) + " of fuel would still be unpaid.";
  } else {
    verdict = "Fuel is covered. Even the best split falls " + money(c.payday_gap_cents) + " short of your payday target.";
  }
  el("offer-explain").textContent = "The customer sends " + money(c.net_cents) + " and keeps a flat " + money(c.flat_fee_cents) + ". " + verdict;
  const fee = el("try-counter-fee"), net = el("try-counter-net");
  fee.hidden = row !== null || c.fee_ceiling_cents === null || c.fee_ceiling_cents >= c.flat_fee_cents;
  net.hidden = row !== null || c.nearest_net_cents === null || c.nearest_net_cents === c.net_cents;
  if (!fee.hidden) fee.textContent = "Try a " + money(c.fee_ceiling_cents) + " fee";
  if (!net.hidden) net.textContent = "Try " + money(c.nearest_net_cents) + " early";
  el("undo-counter").hidden = !originalCounter;
  const comparison = el("compare-wait"), wait = c.wait;
  comparison.hidden = !wait;
  if (wait) {
    el("wait-result").textContent = "With the same " + money(row.cash_cents) + " from you, waiting needs a "
      + money(wait.budget_cents) + " SOL budget to cover fuel. " + (wait.fits ? "At the tested high of " + money(q.high_price_cents)
        + " per SOL, early pay leaves " + paydayChange(wait.stress_difference_cents) + " on payday." : "That exceeds the available SOL limit.");
    el("wait-budget").hidden = !wait.preview_budget_cents;
    if (wait.preview_budget_cents) el("wait-budget").textContent = "Compare with a " + money(wait.budget_cents) + " SOL limit";
    el("wait-prices").hidden = !wait.fits;
    if (wait.fits) {
      showCrossover(wait);
      showPriceCase(wait, c.flat_fee_cents);
    }
    el("wait-history").hidden = !wait.windows;
    if (wait.windows) el("wait-history").textContent = "Across " + wait.windows + " recorded Gemini price moves, the least favorable result for early pay was "
      + paydayChange(wait.lowest[0]) + " at " + money(wait.lowest[1]) + " per SOL. The most favorable was "
      + paydayChange(wait.highest[0]) + " at " + money(wait.highest[1]) + ". Both figures compare payday cash with waiting.";
  }
  const limit = el("offer-bound");
  limit.hidden = false;
  limit.textContent = c.fee_ceiling_cents !== null ? "At " + money(c.net_cents) + " early, the highest flat fee that meets your limits is " + money(c.fee_ceiling_cents) + "."
    : net.hidden ? "Changing just the early amount or just the fee cannot meet these limits." : "This comparison keeps the customer’s flat fee.";
}

function showOffer(q) {
  const offer = q.mainnet.early_freight, chosen = chosenOffer(offer);
  const isCounter = offer.menu === "counter";
  el("offer-ledger").hidden = !chosen;
  el("offer-draft").hidden = !chosen?.advance_cents;
  el("open-counter").hidden = isCounter || !chosen?.advance_cents;
  el("counter-actions").hidden = !isCounter;
  if (chosen) {
    facts(el("offer-ledger"), [[chosen.sale_cents ? "Wallet pays, including " + money(chosen.sale_cents) + " from selling SOL" : "Wallet pays for fuel", money(chosen.wallet_cents)],
      ["Cash kept at the stop", money(inputs().cash - chosen.cash_cents)],
      ["Customer’s early-pay fee", money(chosen.fee_cents)], ["Freight still expected by payday", money(chosen.later_cents)],
      ["Left after restoring funds at " + money(q.high_price_cents) + " per SOL", money(chosen.payday_cents)]]);
  }
  el("range-offer").hidden = !isCounter;
  if (isCounter) { showCounter(q, offer.counter); showRange(q, offer.counter); return; }
  el("compare-wait").hidden = true;
  const toggle = el("offer-switch");
  toggle.hidden = !(chosen && offer.smallest.advance_cents !== offer.keep_cash.advance_cents);
  toggle.textContent = keepRoadCash ? "See smallest request" : "Keep more cash at the stop";
  const tradeoff = el("offer-tradeoff");
  tradeoff.hidden = toggle.hidden;
  if (!toggle.hidden) {
    const difference = offer.keep_cash.fee_cents - offer.smallest.fee_cents;
    tradeoff.textContent = "Taking " + money(offer.keep_cash.advance_cents) + " early keeps "
      + money(offer.smallest.cash_cents - offer.keep_cash.cash_cents) + " more on the road "
      + (difference ? "for an extra " + money(difference) + " in fees." : "at the same fee.");
  }
  el("offer-preview").textContent = chosen?.advance_cents ? money(chosen.advance_cents) + " early" : "";
  if (chosen) {
    el("offer-explain").textContent = (chosen.advance_cents ? (keepRoadCash ? "To keep the most cash at the stop, ask for "
      : "The smallest workable request is ") + money(chosen.advance_cents) + " from the customer before fuel is due."
      : "Your plan already works without an early customer payment.") + " Bring " + money(chosen.cash_cents) + " of your own cash.";

  } else {
    el("offer-explain").textContent = (offer.menu === "none" ? "The customer pays on regular terms." : "No allowed advance covers fuel and your payday target within your limits.")
      + (offer.minimum_cash_cents !== null ? " These terms require at least " + money(offer.minimum_cash_cents) + " of your own cash." : "");
  }
  const justFuel = offer.fuel_only, why = el("offer-fuel-only");
  why.hidden = !(justFuel?.advance_cents && justFuel.payday_cents < q.floor_cents);
  if (!why.hidden) why.textContent = money(justFuel.advance_cents) + " early would cover fuel today, but even the best split at that amount falls "
    + money(q.floor_cents - justFuel.payday_cents) + " below your payday target at the highest tested SOL price.";
  const ceiling = offer.max_fee_per_99_cents;
  el("offer-bound").textContent = ceiling !== null ? "With your cash and wallet limits, the highest fee that works is "
    + money(ceiling) + " per $99 of early cash." : "";
  el("offer-bound").hidden = ceiling === null;
}

function showMix(q) {
  const options = q.mainnet?.secured_loan?.combined;
  el("mix").hidden = !options;
  const link = el("offer-mix"), mixed = options?.plan;
  link.hidden = !mixed?.draw_cents || !mixed.advance_cents;
  if (!link.hidden) link.textContent = options.without_loan
    ? "Compare asking for " + money(mixed.advance_cents) + " with a loan"
    : "See how a " + money(mixed.draw_cents) + " loan covers the gap";
  if (!options) return;
  const key = el("mix-priority").value, r = options[key];
  el("mix-figures").hidden = !r;
  el("mix-request").hidden = !r?.advance_cents;
  if (!r) {
    el("mix-preview").textContent = "Outside these terms";
    el("mix-title").textContent = "This choice falls short";
    el("mix-reason").textContent = key === "without_loan" && options.plan
      ? "With your current limits, covering fuel and keeping " + money(q.floor_cents) + " on payday takes a loan. Switch priorities to see that plan."
      : "The available early payment, cash and wallet budget cannot cover fuel and keep " + money(q.floor_cents) + " at the highest tested SOL price.";
    return;
  }
  el("mix-preview").textContent = r.advance_cents ? money(r.advance_cents) + " early" : "No early pay needed";
  el("mix-title").textContent = r.advance_cents ? "Ask for " + money(r.advance_cents) + " early." : "Keep your invoice on regular terms.";
  const alternative = key === "plan" ? options.without_loan : options.plan;
  const difference = alternative ? Math.abs(alternative.advance_cents - r.advance_cents) : 0;
  el("mix-reason").textContent = difference ? (key === "plan"
    ? "Borrowing " + money(r.draw_cents) + " lets you ask the customer for " + money(difference) + " less."
    : "You ask the customer for " + money(difference) + " more and avoid the " + money(alternative.draw_cents) + " loan.")
    : r.draw_cents ? "This is the smallest loan that works with the smallest available customer payment under your limits."
    : "Your cash and available funds cover the stop without borrowing.";
  const sources = [["Cash you bring", r.cash_cents], ["Customer pays before the stop", r.advance_cents - r.surplus_cents],
    ["Wallet dollars and fuel reserve", r.stable_cents], ["Sell SOL for", r.sale_cents], ["Borrow against SOL", r.draw_cents]];
  facts(el("mix-sources"), [...sources.filter(([, cents]) => cents > 0).map(([name, cents]) => [name, money(cents)]),
    ["Fuel vendor receives", money(q.bill_cents)]]);
  el("mix-payday").textContent = (r.advance_cents ? "The customer keeps " + money(r.fee_cents) + " and owes " + money(r.invoice_balance_cents) + " later. " : "")
    + (r.surplus_cents ? money(r.surplus_cents) + " of the advance stays with you. " : "")
    + (r.draw_cents ? "Estimated loan payoff is " + money(r.repay_cents) + " on day 30. " : "")
    + "After restoring your balances at " + money(q.high_price_cents) + " per SOL, you keep " + money(r.payday_cents) + ".";
  el("mix-risk").textContent = (r.draw_cents ? (r.pledge_lamports / 1e9).toFixed(4) + " SOL stays locked until repayment. Kamino can sell it if its value falls too far. " : "")
    + (r.advance_cents ? "Confirm the amount and date with the customer before relying on it. " : "");
  el("mix-risk").hidden = !r.draw_cents && !r.advance_cents;
  el("mix-assumptions").textContent = "We use the available cash first, then price the remaining fuel at recorded Orca and Kamino terms. Gemini’s historical SOL moves and your extra price set the range for payday."
    + (r.draw_cents ? " The 30-day payoff assumes an existing lending account and twice the recorded variable rate. We include borrowing and repayment fee allowances." : "")
    + " This is a mainnet estimate. Reviewing it moves no funds.";
}

function showLoan(q) {
  const loan = q.mainnet.secured_loan, button = el("loan-budget");
  button.hidden = el("loan-figures").hidden = true;
  el("loan-terms").textContent = "";
  if (!loan) {
    el("loan-preview").textContent = "Unavailable";
    el("loan-verdict").textContent = "We couldn’t verify the recorded Kamino lending terms.";
    return;
  }
  const row = loan.selected, minimum = loan.minimum, sale = loan.sell_only;
  const balance = cents => cents < 0 ? money(-cents) + " short" : money(cents);
  el("loan-preview").textContent = row ? row.draw_cents ? money(row.draw_cents) + " loan" : "No loan needed" : "Outside your limits";
  el("loan-terms").textContent = "Recorded Kamino slot " + loan.slot.toLocaleString("en-US")
    + ". The observed annual borrowing rate was about " + (loan.apr_bps / 100).toFixed(2)
    + "%. We price 30 days at twice that rate and keep stressed debt at least 10 percentage points below the "
    + loan.liquidation_pct + "% liquidation threshold. Orca sale prices come from a separate observation.";
  if (!row) {
    el("loan-verdict").textContent = "With " + money(loan.cash_cents) + " of your cash and no early freight pay, "
      + (loan.best_funded ? "fuel clears, but the best tested split leaves " + balance(loan.best_funded.high_payday_cents)
        + " at the high SOL price." : "your SOL budget cannot cover the fuel gap.")
      + (minimum ? " The smallest SOL budget that also keeps " + money(q.floor_cents) + " on payday is " + money(minimum.opening_cents) + "."
        : " The tested splits cannot meet both goals with the available wallet.");
    if (minimum) {
      button.hidden = false; button.textContent = "Preview a " + money(minimum.opening_cents) + " wallet limit";
    }
    return;
  }
  el("loan-verdict").textContent = row.draw_cents ? "Bring " + money(loan.cash_cents) + " to the "
    + money(q.bill_cents) + " stop. Borrowing " + money(row.draw_cents) + " keeps " + money(q.floor_cents)
    + " on payday even at " + money(loan.high_cents) + " per SOL. A smaller loan misses that target. Your SOL stays locked until repayment."
    : "You can cover this bill and keep your payday target with the cash, reserve and sale available.";
  el("loan-figures").hidden = false;
  facts(el("loan-ledger"), [["Cash you bring", money(loan.cash_cents)], ["Wallet dollars and fuel reserve", money(loan.stable_cents)],
    ["Sell SOL for", money(row.sale_cents)], ["Borrow for fuel", money(row.draw_cents)],
    ["SOL locked until repayment", (row.pledge_lamports / 1e9).toFixed(4) + " SOL"], ["Estimated repayment on day 30", money(row.repay_cents)]]);
  el("loan-outcomes").hidden = !row.draw_cents || !sale;
  el("loan-prices").replaceChildren();
  if (row.draw_cents && sale) {
    for (const [price, key] of [[loan.low_cents, "low_payday_cents"], [q.reference_cents, "start_payday_cents"], [loan.high_cents, "high_payday_cents"]]) {
      const tr = document.createElement("tr");
      for (const value of [money(price), balance(row[key]), balance(sale[key])]) {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      }
      el("loan-prices").append(tr);
    }
    if (loan.history) {
      const h = loan.history;
      el("loan-terms").textContent += " Replaying " + h.windows + " overlapping Gemini 30-day moves, this loan leaves more payday cash than selling in "
        + h.ahead + " cases, with " + h.ties + " ties. This count describes the sample.";
    }
  }
}

function showMainnet(q) {
  const m = q.mainnet, button = el("mainnet-try");
  el("mainnet-check").hidden = false;
  el("mainnet-quoted").hidden = !m;
  showMix(q);
  if (!m) {
    el("mainnet-status").textContent = "Unavailable";
    el("mainnet-result").textContent = "The saved production pool accounts could not be verified. The fuel plan above uses Devnet.";
    return;
  }
  showOffer(q); showLoan(q);
  const missing = m.fuel_short_cents, payday = m.payday_gap_cents;
  el("mainnet-status").textContent = m.secured_loan?.combined?.plan ? "Payment plan available" : missing ? money(missing) + " unpaid" : payday ? money(payday) + " short on payday" : "Fuel and payday covered";
  el("mainnet-result").textContent = "We repriced the full " + money(q.bill_cents) + " bill using two recorded Orca markets. Using cash, reserve money and SOL sales, "
    + (missing ? "your current limits leave " + money(missing) + " of fuel unpaid." : "your current limits cover the fuel bill.");
  const rows = [["Cash needed at the stop", money(m.first_cash_cents)],
    ["Cash needed for fuel and the " + money(q.floor_cents) + " payday target", m.target_cash_cents === null ? "Outside these terms" : money(m.target_cash_cents)]];
  if (m.original) rows.push(["SOL budget needed for the demo’s wallet payment", money(m.original.wallet_limit_cents)]);
  facts(el("mainnet-ledger"), rows);
  const targetPlan = el("mainnet-plan");
  targetPlan.hidden = m.target_cash_cents === null;
  if (!targetPlan.hidden) {
    const wallet = q.bill_cents - m.target_cash_cents;
    targetPlan.textContent = "With " + money(m.target_cash_cents) + " in separate cash, the wallet pays "
      + money(wallet) + (m.target_sale_cents ? ", including " + money(m.target_sale_cents) + " from selling SOL" : "") + ". "
      + money(m.target_plan_payday_cents) + " remains on payday at the highest tested buyback price of " + money(q.high_price_cents) + ".";
  }
  const cash = inputs().cash;
  button.hidden = m.target_cash_cents === null || m.target_cash_cents <= cash;
  if (!button.hidden) button.textContent = "Compare with " + money(m.target_cash_cents) + " available";
  el("mainnet-terms").textContent = "Pool prices were " + m.spot_cents.map(money).join(" and ") + " per SOL at finalized slot "
    + m.slot.toLocaleString("en-US") + " on " + m.date.slice(0,10) + " UTC. The estimate assumes the same cash, reserve and freight terms at full size. "
    + "It includes pool fees, a 0.5% swap allowance, temporary account rent and one base network fee. Mainnet pricing only."
    + (m.covers_full_range ? "" : " Routes are limited to the observed liquidity ranges.");
}

function render(q) {
  last = q;
  const fuelShort = q.status === "fuel_short", paydayShort = q.status === "payday_short";
  const cashUsed = q.separate_cents - q.fuel_gap_cents;
  el("decision").textContent = fuelShort ? money(q.fuel_gap_cents) + " still due" : paydayShort
    ? money(q.payday_gap_cents) + " short on payday" : "Bring " + money(q.separate_cents);
  el("decision-note").textContent = fuelShort
    ? "The wallet could send " + money(q.wallet_cents) + ". Together with " + money(cashUsed) + " of separate cash, it covers " + money(q.bill_cents-q.fuel_gap_cents) + " at the stop."
    : paydayShort ? "You can cover fuel today. Even the best split leaves less than the " + money(q.floor_cents) + " you need on payday."
    : q.preference === "price-room" && q.wallet_cents ? "The wallet could pay the other " + money(q.wallet_cents)
      + ". This split covers the highest SOL buyback price within your limits while meeting your payday target."
    : q.wallet_cents ? "The wallet could pay the other " + money(q.wallet_cents) + ". This is the least cash you need to bring while protecting your payday target."
    : "Separate cash covers the full bill. You can leave the wallet untouched.";
  el("source").textContent = q.source === "live" ? (q.payment_slot ? "Live Devnet simulation" : "Fresh Devnet prices") : "Saved Devnet prices";
  const amounts = [cashUsed, q.wallet_cents, q.fuel_gap_cents];
  el("funding").querySelectorAll(".stop-strip i").forEach((bar,i) => { bar.style.width = 100 * amounts[i] / q.bill_cents + "%"; });
  el("fuel-legs").textContent = amounts.map((amt,i) => amt ? money(amt) + [" separate cash", " from wallet", " unpaid"][i] : "").filter(Boolean).join(" · ");
  el("kept").textContent = money(q.untouched_cents);
  el("payday").textContent = fuelShort ? "Fuel unpaid" : money(q.low_payday_cents);
  el("funding").hidden = el("sources").hidden = false;
  facts(el("wallet-parts"), [["Wallet cash", money(q.wallet_cash_cents)], ["From the Kamino fuel reserve", money(q.reserve_cents)],
    ["From selling SOL through Orca", money(q.exchange_cents)], ["Wallet payment to the fuel company", money(q.wallet_cents)]]);
  el("math").textContent = "At least " + money(q.protected_cents) + " stays budgeted for the next stop. The estimate restores your separate cash, wallet cash, reserve and SOL from " + money(q.receipt_cents)
    + " of freight receipts. SOL buyback prices range from " + money(q.low_price_cents) + " to " + money(q.high_price_cents) + ". The wallet limit values SOL at " + money(q.reference_cents) + " per SOL at demo scale. Exchange proceeds use the pool’s Devnet price."
    + (q.wallet_capped ? " The observed wallet limits the usable budget to " + money(q.effective_budget_cents) + "." : "");
  el("proof").textContent = (q.source === "live" ? "Public" : "Recorded") + " pool accounts at slot " + q.curve_slot.toLocaleString("en-US") + ". "
    + (q.payment_slot ? "The exact wallet transfer was simulated at slot " + q.payment_slot.toLocaleString("en-US") + "."
      : q.wallet_cents ? "This exact transfer is priced from the pool accounts." : "This split uses no wallet transfer.");
  repairs(q); showPriceRoom(q); showMainnet(q);
  if (focusOffer && q.mainnet) {
    revealOffer();
    const panel = focusRange ? el("range-offer") : el("compare-wait");
    if ((focusWait || focusRange) && !panel.hidden) {
      panel.open = true;
      const summary = panel.querySelector("summary");
      summary.focus({preventScroll: true}); summary.scrollIntoView({block: "start"});
    }
  }
  if (focusLoan && q.mainnet) {
    const summary = el("loan-result").querySelector("summary");
    summary.focus({preventScroll: true}); summary.scrollIntoView({block: "start"});
  }
  if (focusMix && q.mainnet?.secured_loan?.combined) {
    const summary = el("mix").querySelector("summary");
    summary.focus({preventScroll: true}); summary.scrollIntoView({block: "start"});
  }
  focusMix = focusLoan = focusRange = focusWait = focusOffer = false;
  if (focusMainnet) {
    el("mainnet-check").querySelector("summary").focus({preventScroll: true});
    focusMainnet = false;
  }
  note.textContent = q.source === "live" ? (q.payment_slot ? "Wallet payment checked on Devnet. No money moved." : "Public pool prices checked. No money moved.")
    : "Planning estimate from saved public data. Editing moves no money.";
}

async function price(source, current, id) {
  const controller = new AbortController();
  pending = controller;
  const timeout = setTimeout(() => controller.abort(), source === "live" ? 95000 : 15000);
  verify.disabled = true;
  try {
    const q = await request("/api/fuel-stop", {...current, source, preference}, controller.signal);
    if (id === revision) render(q);
  } catch (error) {
    if (id !== revision) return;
    if (source === "live" && last) {
      note.textContent = "Public check unavailable. The previous " + (last.source === "live" ? "Devnet check" : "saved-price estimate") + " is still shown.";
    } else {
      last = null;
      el("decision").textContent = "Estimate unavailable";
      el("decision-note").textContent = "Review the amounts and try again.";
      el("sources").hidden = el("changes").hidden = el("funding").hidden = true;
      note.textContent = error.message;
    }
  } finally {
    clearTimeout(timeout);
    if (id === revision) { pending = null; verify.disabled = !inputs(); el("stop-outcome").removeAttribute("aria-busy"); }
  }
}

function estimate(delay = 0) {
  const id = ++revision;
  clearTimeout(timer); pending?.abort(); last = null;
  verify.disabled = true; el("fixes").replaceChildren(); el("changes").hidden = el("mainnet-check").hidden = el("price-room").hidden = true;
  const current = inputs();
  if (!current) {
    el("decision").textContent = "Check your amounts";
    el("decision-note").textContent = "Use dollars and cents within the limits shown for each field.";
    el("funding").hidden = el("sources").hidden = true; el("stop-outcome").removeAttribute("aria-busy"); note.textContent = ""; return;
  }
  el("stop-outcome").setAttribute("aria-busy", "true");
  note.textContent = "Updating your estimate…";
  timer = setTimeout(() => price("recorded", current, id), delay);
}

function revealOffer() {
  el("mainnet-check").open = el("offer-result").open = true;
  const summary = el("offer-result").querySelector("summary");
  summary.focus({preventScroll: true});
  summary.scrollIntoView({block: "start"});
}
el("review-offer").onclick = () => {
  if (!form.reportValidity()) return;
  if (last?.mainnet) revealOffer();
  else if (!last) focusOffer = true;
  else {
    el("mainnet-check").open = true;
    el("mainnet-check").querySelector("summary").focus();
  }
};
function updateOfferFields() {
  const menu = el("offer-menu").value, exact = menu === "counter";
  el("counter-fields").hidden = el("counter-fields").disabled = !exact;
  el("scaled-terms").hidden = exact;
  el("offer_fee").disabled = exact || menu === "none";
}
function compareCounter(field, cents) {
  originalCounter ??= [el("offer_amount").value, el("flat_fee").value];
  el(field).value = (cents / 100).toFixed(2);
  focusOffer = true; estimate();
}
el("wait-scenario").onchange = () => {
  const c = last?.mainnet?.early_freight.counter;
  if (c?.wait?.fits) showPriceCase(c.wait, c.flat_fee_cents);
};
el("price-fee").onclick = () => {
  const limit = last?.mainnet?.early_freight.counter?.wait?.[el("wait-scenario").value]?.fee_limit_cents;
  if (limit != null) { focusWait = true; compareCounter("flat_fee", limit); }
};
el("wait-budget").onclick = () => {
  const budget = last?.mainnet?.early_freight.counter?.wait?.preview_budget_cents;
  if (budget) { focusWait = focusOffer = true; tryAmount("budget", budget); }
};
el("try-counter-fee").onclick = () => compareCounter("flat_fee", last.mainnet.early_freight.counter.fee_ceiling_cents);
el("try-counter-net").onclick = () => compareCounter("offer_amount", last.mainnet.early_freight.counter.nearest_net_cents);
el("undo-counter").onclick = () => {
  [el("offer_amount").value, el("flat_fee").value] = originalCounter;
  originalCounter = null; focusOffer = true; estimate();
};
el("open-counter").onclick = () => {
  const row = chosenOffer(last.mainnet.early_freight);
  el("offer-menu").value = "counter";
  el("offer_amount").value = (row.advance_cents / 100).toFixed(2);
  el("flat_fee").value = (row.fee_cents / 100).toFixed(2);
  updateOfferFields(); el("offer-inputs").open = true; el("offer_amount").focus();
  estimate();
};
el("edit-offer").onclick = () => {
  el("offer-inputs").open = true;
  el("offer-menu").focus();
};
el("offer-switch").onclick = () => {
  if (!last?.mainnet) return;
  keepRoadCash = !keepRoadCash;
  showOffer(last);
};
function downloadOffer(row) {
  if (!row?.advance_cents || row.fee_cents === null) return;
  const draft = [
    "Draft early-payment request · FreightOS sample load FOS-2048", "",
    "Could you send " + money(row.advance_cents) + " before the fuel stop on this load?",
    "With an early-payment discount of " + money(row.fee_cents) + ", that would settle "
      + money(row.advance_cents + row.fee_cents) + " of freight charges and leave " + money(row.invoice_balance_cents) + " on regular terms.",
    "Please confirm the amount, discount and arrival time before we rely on the payment.", ""
  ];
  saveFile("FOS-2048-early-payment-request.txt", draft.join("\n"), "text/plain;charset=utf-8");
}
el("offer-draft").onclick = () => {
  if (last?.mainnet) downloadOffer(chosenOffer(last.mainnet.early_freight));
};
el("fee-range").onchange = () => {
  const counter = last?.mainnet?.early_freight.counter;
  if (counter) showRange(last, counter);
};
el("range-draft").onclick = () => downloadOffer(last?.mainnet?.early_freight.counter?.ranges[el("fee-range").value]?.split);
el("offer-mix").onclick = () => {
  el("mix-priority").value = "plan";
  showMix(last);
  el("mix").open = true;
  const summary = el("mix").querySelector("summary");
  summary.focus(); summary.scrollIntoView({block: "start"});
};
el("mix-priority").onchange = () => { if (last) showMix(last); };
el("mix-request").onclick = () => downloadOffer(last?.mainnet?.secured_loan?.combined?.[el("mix-priority").value]);
el("mix-edit").onclick = () => { el("offer-inputs").open = true; el("offer-menu").focus(); };
el("loan-budget").onclick = () => {
  const budget = last?.mainnet?.secured_loan?.minimum?.opening_cents;
  if (budget) { focusLoan = true; tryAmount("budget", budget); }
};
el("mainnet-try").onclick = () => {
  if (last?.mainnet?.target_cash_cents != null) {
    focusMainnet = true;
    tryAmount("cash", last.mainnet.target_cash_cents);
  }
};
el("price-choice").onclick = () => {
  preference = preference === "keep-cash" ? "price-room" : "keep-cash";
  estimate(); el("decision").focus();
};
document.querySelector(".stop-edit").onclick = event => {
  event.preventDefault();
  el("cash").focus();
};
form.oninput = () => { undo = null; keepRoadCash = false; originalCounter = null; updateOfferFields(); estimate(140); };
form.onsubmit = event => { event.preventDefault(); estimate(); };
verify.onclick = () => {
  const current = inputs(); if (!current) return;
  clearTimeout(timer); pending?.abort();
  note.textContent = "Checking the wallet, reserve and vendor payment on public Solana…";
  price("live", current, ++revision);
};
function openPreset() {
  const preset = new URLSearchParams(location.hash.slice(1));
  form.reset();
  keepRoadCash = false;
  el("offer-menu").value = ["none", "both", "counter"].includes(preset.get("terms")) ? preset.get("terms") : "partial";
  originalCounter = null; updateOfferFields();
  preference = preset.get("preference") === "price-room" ? "price-room" : "keep-cash";
  undo = null;
  for (const input of form.querySelectorAll("input")) {
    const value = preset.get(input.id);
    if (value !== null && /^\d{1,7}(?:\.\d{1,2})?$/.test(value) && Number(value) <= Number(input.max) && Number(value) >= Number(input.min))
      input.value = Number(value).toFixed(2);
  }
  el("price-room").open = preset.has("preference");
  el("mainnet-check").open = ["mainnet", "early-pay", "loan", "funding"].includes(preset.get("market"));
  el("mix-priority").value = "plan";
  el("mix").open = focusMix = preset.get("market") === "funding";
  el("loan-result").open = focusLoan = preset.get("market") === "loan";
  focusOffer = preset.get("market") === "early-pay";
  el("offer-result").open = focusOffer;
  focusWait = focusOffer && preset.get("view") === "buyback";
  el("compare-wait").open = focusWait;
  focusRange = focusOffer && preset.get("view") === "fee-range";
  el("range-offer").open = focusRange;
  el("fee-range").value = "full";
  el("wait-scenario").value = "starting_price";
  estimate();
}
window.addEventListener("hashchange", openPreset);
openPreset();
