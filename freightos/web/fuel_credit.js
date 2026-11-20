import { el, facts, money, request } from "./common.js";

let sequence = 0;
const sol = lamports => (lamports / 1e9).toLocaleString("en-US", { maximumFractionDigits: 9 }) + " SOL";
const pct = bps => (bps / 100).toFixed(2) + "%";

function display(d) {
  el("credit-cost").textContent = money(d.cost_cents);
  el("credit-repay").textContent = "Repay about " + money(d.repay_cents) + " from the $200 invoice in " + d.days + " days.";
  const room = d.fee_headroom_cents;
  facts(el("credit-compare"), [["Fee to collect the invoice early", "$10.00"],
    [room >= 0 ? "Room for other costs before early pay costs less" : "Loan cost above early pay", money(Math.abs(room))],
    ["SOL pledged at full bill scale", sol(d.collateral_lamports * 100) + " (about " + money(Math.round(d.collateral_lamports / 1e9 * 100 * Number(d.sol_price) * 100)) + ")"]]);
  el("credit-pledge").textContent = "At a " + d.drop + "% price drop, the loan would use " + pct(d.stress_ltv_bps) + " of the collateral’s value by payday.";
  facts(el("credit-risk"), [["SOL price in the check", "$" + d.sol_price], ["Liquidation price by payday", money(d.liquidation_price_cents)]]);
  el("credit-proof").textContent = "At slot " + d.slot.toLocaleString("en-US") + ", Kamino accepted " + sol(d.collateral_lamports)
    + " of test SOL, opened a debt and paid 1.90 test USDC to the named demo vendor in one simulation. One test dollar represents $100 on this bill.";
  el("credit-assumptions").textContent = "The estimate holds the reserve’s " + pct(d.rate_bps) + " configured annual borrowing rate and USDC price constant. "
    + "The pledge leaves 10 percentage points of room below liquidation after the chosen drop. The draw included a "
    + sol(d.fee_lamports) + " network fee and used " + sol(d.setup_lamports) + " for account setup. Setup, network, repayment transaction and conversion costs are excluded from the modeled financing cost.";
  el("credit-result").hidden = false;
}

async function compare(source) {
  const attempt = ++sequence;
  const body = { drop: Number(el("credit-drop").value), days: Number(el("credit-days").value), source };
  const button = el("credit-check");
  button.disabled = true;
  el("credit-status").textContent = source === "live" ? "Checking collateral, loan and vendor payment on Devnet…" : "Opening the recorded simulation…";
  try {
    const data = await request("/api/fuel-credit", body, AbortSignal.timeout(45000));
    if (attempt !== sequence) return;
    display(data);
    const time = new Date(data.checked_at).toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
    el("credit-status").textContent = (source === "live" ? "Public Devnet check" : "Recorded Devnet check") + " · " + time + " UTC";
    if (source === "live" && matchMedia("(max-width: 720px)").matches) el("credit-heading").focus();
  } catch {
    if (attempt !== sequence) return;
    const previous = !el("credit-result").hidden;
    el("credit-status").textContent = previous ? "Live check unavailable. The previous quote for these terms is shown." : "Quote unavailable. Try checking again.";
  } finally {
    if (attempt === sequence) button.disabled = false;
  }
}
el("credit-form").onchange = () => { el("credit-result").hidden = true; compare("recorded"); };
el("credit-form").onsubmit = event => { event.preventDefault(); compare("live"); };
compare("recorded");
