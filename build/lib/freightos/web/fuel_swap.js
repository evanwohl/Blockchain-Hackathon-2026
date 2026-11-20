import { el, facts, money, request } from "./common.js";

const sol = value => (value / 1e9).toLocaleString("en-US", {maximumFractionDigits: 9}) + " test SOL";
let revision = 0;
let terms;
let previous = "";
let lastSource = "recorded";

export function clearSale() {
  revision++;
  terms = null;
  previous = "";
  el("sale-result").hidden = true;
  el("sale-status").textContent = "";
}

function display(q) {
  const s = q.sale, value = raw => money(raw / q.scale);
  el("sale-heading").textContent = q.sale_short_lamports ? "The sale exceeds your wallet limit" : "You can cover fuel without a loan";
  el("sale-total").textContent = value(s.vendor_raw);
  el("sale-action").textContent = (q.customer_cents ? "Pay " + money(q.customer_cents) + " separately once the customer cash clears. " : "")
    + (s.spent_lamports ? "The exchange supplies " + value(s.swap_raw) + " and your wallet and reserve cover the rest." : "Your wallet and reserve cover the payment.");
  facts(el("sale-facts"), [
    ["Wallet cash used for fuel", value(s.wallet_raw)], ["Kamino reserve used", value(s.reserve_raw)],
    ["SOL sold for fuel", sol(s.spent_lamports)], ["Kept for the next fuel stop", value(s.retained_raw)],
    ["Wallet needed for the sale, including temporary funds", sol(s.entry_lamports)],
    ["Wallet needed to borrow for the same payment", sol(q.loan_entry)]
  ]);
  if (q.sale_short_lamports) el("sale-action").textContent += " Your limit is short by " + sol(q.sale_short_lamports) + ".";
  else if (q.loan_short_lamports) el("sale-action").textContent += " Borrowing would need another " + sol(q.loan_short_lamports) + " above your limit.";
  const difference = Math.abs(q.difference_cents);
  el("sale-payday").textContent = "If you sell, the remaining freight invoice leaves " + money(q.sale_cash_after_refill_cents) + " after refilling the reserve. At "
    + money(q.price_cents) + " per SOL, replacing the SOL and restoring the wallet cash leaves " + money(q.sale_remaining_cents)
    + ". Repaying the stressed loan instead leaves " + money(q.loan_remaining_cents) + ". "
    + (difference ? "The " + (q.difference_cents > 0 ? "sale" : "loan") + " leaves " + money(difference) + " more at that price." : "The two leave the same amount.");
  el("sale-boundary").textContent = (q.first_higher_cents === null ? "" : "At " + money(q.first_higher_cents) + " per SOL or above, replacing what you sold costs more than repaying the loan. ")
    + "The default uses the observed Kamino SOL price. Enter an all-in replacement price to include a future exchange fee. The loan comparison assumes repayment, collateral release and full value for loan account deposits.";
  facts(el("sale-pools"), [...q.pools.map(r => [r.pool ? "Orca pool " + r.pool.slice(0, 6) + "…" : "Wallet cash", sol(r.spent_lamports)]), ...q.declined.map(pool => ["Orca pool " + pool.slice(0, 6) + "…", "Could not fill this sale"])]);
  el("sale-check").textContent = "Vendor payment at slot " + s.slot.toLocaleString("en-US") + ". The capped sale allows up to " + sol(s.limit_lamports)
    + " for the exchange. Setup returns " + sol(s.rent_lamports) + " when the temporary account closes. Another simulation checked the remaining vault shares. These test-pool prices can differ substantially from the loan oracle.";
  el("sale-result").hidden = false;
  if (!el("sale-primary").hidden) {
    el("credit-heading").textContent = el("sale-heading").textContent;
    el("credit-jump").textContent = el("credit-heading").textContent + " · see payment ↓";
    el("credit-jump").hidden = false;
  }
}

async function refresh(source) {
  if (!terms || !el("fuel-sale").open) return;
  const attempt = ++revision, price = el("sale-price");
  if (!price.validity.valid) { el("sale-status").textContent = "Enter a valid replacement price."; return; }
  el("sale-live").disabled = true;
  el("sale-status").textContent = source === "live" ? "Checking the two pools, vendor and loan on Devnet…" : "Replaying the recorded vendor payment…";
  try {
    const q = await request("/api/fuel-swap", {...terms, source, customer_cents: Number(el("sale-customer").value),
      price_cents: price.value === "" ? null : Math.round(price.valueAsNumber * 100)}, AbortSignal.timeout(90000));
    if (attempt !== revision) return;
    display(q);
    lastSource = source;
    previous = (source === "live" ? "Public Devnet simulation" : "Recorded Devnet simulation") + " · no funds moved.";
    el("sale-status").textContent = previous;
  } catch {
    if (attempt === revision) el("sale-status").textContent = "Exchange check unavailable. " + (previous ? previous + " Previous result shown." : "Try refreshing the quote.");
    if (attempt === revision && !previous && !el("sale-primary").hidden) el("credit-heading").textContent = "Payment quote unavailable";
  } finally { if (attempt === revision) el("sale-live").disabled = false; }
}

export function setupSale(data, body, direct = false) {
  if (direct && terms && ["menu", "fee_cents", "wallet_lamports", "extra_cents"].every(key => terms[key] === body[key])) {
    return refresh(body.source);
  }
  clearSale();
  terms = body;
  lastSource = body.source;
  el("sale-customer").replaceChildren(...data.options.map(r => new Option(money(r.advance_cents), r.advance_cents)));
  el("sale-customer").closest("label").hidden = data.options.length < 2;
  el("sale-terms").querySelector("summary").textContent = data.options.length < 2 ? "Change the payday buyback price" : "Change the payday price or customer payment";
  el("sale-customer").value = data.options.some(r => r.advance_cents === data.choice) ? data.choice : data.options.at(-1).advance_cents;
  el("sale-live").disabled = false;
  if (direct) el("fuel-sale").open = true;
  if (el("fuel-sale").open) return refresh(body.source);
}
function edit() {
  if (!el("sale-primary").hidden) {
    el("credit-heading").textContent = "Your payment estimate";
    el("credit-jump").hidden = true;
  }
  ++revision;
  previous = "";
  el("sale-result").hidden = true;
  el("sale-live").disabled = false;
}
el("sale-price").oninput = () => { edit(); el("sale-status").textContent = "Finish editing to update the comparison."; };
el("sale-price").onchange = el("sale-customer").onchange = () => { edit(); refresh(lastSource); };
el("fuel-sale").ontoggle = () => { if (!el("sale-primary").hidden) return; if (el("fuel-sale").open && el("sale-result").hidden) refresh(lastSource); };
el("sale-live").onclick = () => refresh("live");
