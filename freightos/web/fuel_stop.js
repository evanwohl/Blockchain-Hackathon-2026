import {el, facts, money, request, saveFile} from "./common.js";

const form = el("stop-inputs"), verify = el("verify"), note = el("check-status");
let focusMainnet = false, focusOffer = false, keepRoadCash = false;
let preference = "keep-cash";
let revision = 0, timer, pending, last, undo;

function inputs() {
  if (!form.checkValidity()) return null;
  const amounts = Object.fromEntries(["cash", "budget", "target", "received", "stress"].map(id => {
    const input = el(id);
    return [id, input.value === "" ? null : Math.round(input.valueAsNumber * 100)];
  }));
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
  return keepRoadCash ? offer.keep_cash : offer.smallest;
}

function showOffer(q) {
  const offer = q.mainnet.early_freight, chosen = chosenOffer(offer);
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
  el("offer-ledger").hidden = !chosen;
  el("offer-draft").hidden = !chosen?.advance_cents;
  el("offer-preview").textContent = chosen?.advance_cents ? money(chosen.advance_cents) + " early" : "";
  if (chosen) {
    el("offer-explain").textContent = (chosen.advance_cents ? (keepRoadCash ? "To keep the most cash at the stop, ask for "
      : "The smallest workable request is ") + money(chosen.advance_cents) + " from the customer before fuel is due."
      : "Your plan already works without an early customer payment.") + " Bring " + money(chosen.cash_cents) + " of your own cash.";
    facts(el("offer-ledger"), [[chosen.sale_cents ? "Wallet pays, including " + money(chosen.sale_cents) + " from selling SOL" : "Wallet pays for fuel", money(chosen.wallet_cents)],
      ["Cash kept at the stop", money(inputs().cash - chosen.cash_cents)],
      ["Customer’s early-pay fee", money(chosen.fee_cents)], ["Freight still expected by payday", money(chosen.later_cents)],
      ["Left after restoring funds at " + money(q.high_price_cents) + " per SOL", money(chosen.payday_cents)]]);
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

function showMainnet(q) {
  const m = q.mainnet, button = el("mainnet-try");
  el("mainnet-check").hidden = false;
  el("mainnet-quoted").hidden = !m;
  if (!m) {
    el("mainnet-status").textContent = "Unavailable";
    el("mainnet-result").textContent = "The saved production pool accounts could not be verified. The fuel plan above uses Devnet.";
    return;
  }
  showOffer(q);
  const missing = m.fuel_short_cents, payday = m.payday_gap_cents;
  el("mainnet-status").textContent = missing ? money(missing) + " unpaid" : payday ? money(payday) + " short on payday" : "Fuel and payday covered";
  el("mainnet-result").textContent = "We repriced the full " + money(q.bill_cents) + " bill using two recorded Orca markets. Without early customer pay, "
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
  if (focusOffer && q.mainnet) revealOffer();
  focusOffer = false;
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
el("edit-offer").onclick = () => {
  el("offer-inputs").open = true;
  el("offer-menu").focus();
};
el("offer-switch").onclick = () => {
  if (!last?.mainnet) return;
  keepRoadCash = !keepRoadCash;
  showOffer(last);
};
el("offer-draft").onclick = () => {
  if (!last?.mainnet) return;
  const row = chosenOffer(last.mainnet.early_freight);
  if (!row?.advance_cents) return;
  const draft = [
    "Draft early-payment request · FreightOS sample load FOS-2048", "",
    "Could you send " + money(row.advance_cents) + " before the fuel stop on this load?",
    "With an early-payment discount of " + money(row.fee_cents) + ", that would settle "
      + money(row.advance_cents + row.fee_cents) + " of freight charges and leave " + money(row.invoice_balance_cents) + " on regular terms.",
    "Please confirm the amount, discount and arrival time before we rely on the payment.", ""
  ];
  saveFile("FOS-2048-early-payment-request.txt", draft.join("\n"), "text/plain;charset=utf-8");
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
form.oninput = () => { undo = null; keepRoadCash = false; el("offer_fee").disabled = el("offer-menu").value === "none"; estimate(140); };
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
  el("offer-menu").value = ["none", "both"].includes(preset.get("terms")) ? preset.get("terms") : "partial";
  el("offer_fee").disabled = el("offer-menu").value === "none";
  preference = preset.get("preference") === "price-room" ? "price-room" : "keep-cash";
  undo = null;
  for (const input of form.querySelectorAll("input")) {
    const value = preset.get(input.id);
    if (value !== null && /^\d{1,7}(?:\.\d{1,2})?$/.test(value) && Number(value) <= Number(input.max))
      input.value = Number(value).toFixed(2);
  }
  el("price-room").open = preset.has("preference");
  el("mainnet-check").open = ["mainnet", "early-pay"].includes(preset.get("market"));
  focusOffer = preset.get("market") === "early-pay";
  el("offer-result").open = focusOffer;
  estimate();
}
window.addEventListener("hashchange", openPreset);
openPreset();
