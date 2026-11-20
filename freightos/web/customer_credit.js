import { el, facts, money, saveFile } from "./common.js";

const sol = value => (value / 1e9).toLocaleString("en-US", {maximumFractionDigits: 9}) + " test SOL";

function gaps(row, funds) {
  const parts = [];
  if (row.entry_lamports > funds) parts.push(sol(row.entry_lamports - funds) + " short today");
  if (row.payday_cash_cents < 0) parts.push(money(-row.payday_cash_cents) + " short on payday");
  return parts.join(" and ");
}

export function displayCustomer(data) {
  const row = data.options.find(option => option.advance_cents === data.choice);
  const base = data.options[0];
  facts(el("customer-comparison"), data.options.map(option => [
    option.advance_cents ? money(option.advance_cents) + " from customer" : "Wait for the full invoice",
    money(option.cost_cents) + " estimated cost · " + (gaps(option, data.wallet_lamports) || "fits")
  ]));
  el("customer-request").hidden = !row?.advance_cents;
  el("customer-alternatives").open = !row;
  el("customer-backup").hidden = !row?.advance_cents;
  if (row?.advance_cents) {
    const short = gaps(base, data.wallet_lamports);
    el("customer-delay").textContent = "Before taking any loan, check that the customer cash has cleared. Without it you would need a " + money(base.borrow_cents)
      + " loan alongside " + money(base.reserve_cents) + " from the reserve. "
      + (short ? "This fallback is " + short + "." : "It fits the entered wallet and would cost an estimated " + money(base.cost_cents) + ".");
  }
  const funding = el("customer-funding");
  funding.hidden = !row;
  if (row) {
    const segments = funding.querySelectorAll("i"), amounts = funding.querySelectorAll("dd");
    [row.advance_cents, row.reserve_cents, row.borrow_cents].forEach((amount, index) => {
      segments[index].style.width = (100 * amount / data.bill_cents) + "%";
      amounts[index].textContent = money(amount);
    });
    el("credit-heading").textContent = row.advance_cents ? "Collect " + money(row.advance_cents) + " early" : "Wait for the full invoice";
    el("customer-label").textContent = "Borrow for this fuel stop";
    el("customer-amount").textContent = money(row.borrow_cents);
    el("customer-action").textContent = (row.advance_cents ? "Once the customer’s " + money(row.advance_cents) + " clears, pay that amount toward fuel. " : "")
      + "The reserve and loan can pay the vendor the remaining " + money(row.borrow_cents + row.reserve_cents) + " together.";
    facts(el("customer-breakdown"), [
      ["Fuel still due after both payments clear", money(0)],
      ["Estimated cost including fees and missed earnings", money(row.cost_cents)],
      ["Test SOL needed now, including account setup", sol(row.entry_lamports)]
    ]);
    el("customer-payday").textContent = "The customer still owes " + money(row.invoice_left_cents) + ". Allow "
      + money(row.payoff_cents) + " for loan payoff and " + money(row.reserve_cents) + " to refill the reserve. After entered expenses you keep "
      + money(row.payday_cash_cents) + " if the invoice clears by day 30.";
    const diff = base.same_rate_cost_cents - row.same_rate_cost_cents;
    el("customer-rate").textContent = row.advance_cents ? "If all draws carried the same stressed rate as borrowing without customer cash, this plan would cost "
      + money(row.same_rate_cost_cents) + " versus " + money(base.same_rate_cost_cents) + " waiting"
      + (diff > 0 ? ", a " + money(diff) + " difference." : ".") : "The full invoice stays payable on its original terms.";
    if (data.next_change) {
      const next = data.next_change;
      const action = next.advance_cents === null ? "none of the offers fits" : next.advance_cents ? "taking " + money(next.advance_cents) + " early becomes the best fit" : "waiting for the full invoice becomes the best fit";
      el("customer-rate").textContent += " If the fee per $99 reaches " + money(next.fee_cents) + ", " + action + ".";
    }
    el("customer-check").textContent = "Slot " + row.slot.toLocaleString("en-US") + ". Opening uses " + sol(row.pledge_lamports)
      + " in collateral, " + sol(row.setup_lamports) + " for account setup and " + sol(row.network_lamports) + " in network fees. The "
      + money(row.cost_cents) + " estimate includes that opening fee at sample scale (" + money(row.opening_fee_cents)
      + "), the customer fee (" + money(row.fee_cents) + ") and your entered expenses (" + money(row.extra_cents) + "). "
      + money(row.retained_cents) + " stays available for three gallons under the recorded Gemini oil scenario.";
    el("customer-request").onclick = () => saveFile("FreightOS-customer-contribution.txt",
      "Freight invoice [invoice or load ID]\n\nPlease confirm that " + money(row.advance_cents)
      + " can clear by [fuel purchase date]. Including your fee of " + money(row.fee_cents)
      + ", this would leave " + money(row.invoice_left_cents) + " of the $200 invoice payable on [regular payment date].\n", "text/plain");
  } else {
    el("credit-heading").textContent = "These offers leave a gap";
    el("customer-label").textContent = "Fuel still to arrange";
    el("customer-amount").textContent = money(data.bill_cents);
    el("customer-action").textContent = "None of the checked plans covers the entire bill within your available SOL and the customer’s remaining invoice. No money has moved.";
    facts(el("customer-breakdown"), [["Available now for opening the loan", sol(data.wallet_lamports)],
      ["Reserved for three more gallons", money(data.holdback.cost_cents)]]);
    el("customer-payday").textContent = "Each comparison checks both what you can put up today and what you can repay when freight pays.";
    el("customer-rate").textContent = "Each shortfall uses the customer payments you selected.";
    el("customer-check").textContent = "The " + money(data.holdback.cost_cents) + " next-stop budget comes from the recorded Gemini oil scenario.";
  }
  el("customer-result").hidden = false;
}
