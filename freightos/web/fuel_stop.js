import {el, facts, money, request} from "./common.js";

const form = el("stop-inputs"), verify = el("verify"), note = el("check-status");
let preference = "keep-cash";
let revision = 0, timer, pending, last, undo;

function inputs() {
  if (!form.checkValidity()) return null;
  return Object.fromEntries(["cash", "budget", "target", "received", "stress"].map(id => {
    const input = el(id);
    return [id, input.value === "" ? null : Math.round(input.valueAsNumber * 100)];
  }));
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
      const field = el(fix.input);
      undo = {field, value: field.value};
      if (fix.input === "received") field.closest("details").open = true;
      field.value = (fix.amount_cents / 100).toFixed(2);
      estimate(); el("decision").focus({preventScroll: true});
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
  repairs(q); showPriceRoom(q);
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
  verify.disabled = true; el("fixes").replaceChildren(); el("changes").hidden = el("price-room").hidden = true;
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

el("price-choice").onclick = () => {
  preference = preference === "keep-cash" ? "price-room" : "keep-cash";
  estimate(); el("decision").focus();
};
document.querySelector(".stop-edit").onclick = event => {
  event.preventDefault();
  el("cash").focus();
};
form.oninput = () => { undo = null; estimate(140); };
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
  preference = preset.get("preference") === "price-room" ? "price-room" : "keep-cash";
  undo = null;
  for (const input of form.querySelectorAll("input")) {
    const value = preset.get(input.id);
    if (value !== null && /^\d{1,7}(?:\.\d{1,2})?$/.test(value) && Number(value) <= Number(input.max))
      input.value = Number(value).toFixed(2);
  }
  el("price-room").open = preset.has("preference");
  estimate();
}
window.addEventListener("hashchange", openPreset);
openPreset();
