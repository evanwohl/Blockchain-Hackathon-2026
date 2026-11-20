import {el, facts, money, request} from "./common.js";

const minSol = raw => (Math.floor(raw / 1000) / 1e6).toFixed(6) + " SOL";
const maxSol = raw => (Math.ceil(raw / 1000) / 1e6).toFixed(6) + " SOL";
let jumpToPayoff = new URLSearchParams(location.search).get("view") === "payoff";

function selection(row) {
  return row.payoff_options.find(o => o.keep_pct === Number(el("exit-keep").value));
}

export function showExit(rows, low) {
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
    const [name, row] = stages[Number(stage.value)], o = selection(row);
    facts(el("exit-amounts"), [["Cash needed for payoff", money(o.cash_cents)], ["SOL you keep, at least", minSol(o.min_left_lamports)]]);
    el("exit-how").textContent = name + " · " + money(row.repay_cents) + " estimated payoff on day 30. " + (o.credit_cents
      ? "At the recorded pool prices, selling up to " + maxSol(o.max_sol_lamports) + " raises " + money(o.sale_cents) + " toward closing the loan."
      : "Paying this amount in USDC releases the collateral in full.") + " Network costs are separate.";
    el("exit-price").hidden = o.keep_pct === 100;
    el("exit-price").textContent = "At the lowest tested price of " + money(low) + " per SOL, "
      + (o.low_price_cash_floor_cents ? "you would need at least " + money(o.low_price_cash_floor_cents) + " in cash, even before sale costs."
        : "the permitted collateral covers the payoff on paper. The actual sale still depends on available liquidity.");
  }
  stage.onchange = el("exit-keep").onchange = update;
  update();
  if (jumpToPayoff) {
    jumpToPayoff = false;
    check.open = true;
    check.querySelector("summary").focus();
  }
}

export function exitLines(row) {
  if (!row.payoff_options) return [];
  const o = selection(row);
  return ["Optional single-stop payoff at recorded prices. Keep at least " + o.keep_pct + "% of pledged SOL.",
    "Driver USDC needed: " + money(o.cash_cents) + ". Temporary credit: " + money(o.credit_cents)
      + ". Orca sale output: " + money(o.sale_cents) + ". Maximum SOL sold: " + o.max_sol_lamports + " lamports.",
    "Flash fee allowance: " + (o.fee_cap_raw / 1e6).toFixed(6) + " USDC. Sale proceeds above the payoff and fee stay in the wallet.",
    "Pledged SOL left: at least " + o.min_left_lamports + " lamports. Separate network fees and temporary account deposits apply. Reprice the next stop after using collateral."];
}

let lastProof;
async function showProof(live) {
  const status = el("exit-proof-result"), button = el("exit-simulate");
  button.disabled = true;
  status.textContent = live ? "Checking payoff and sale limits on public Devnet…" : "Reading the saved Devnet payoff…";
  try {
    const p = await request("/api/collateral-exit" + (live ? "/simulate" : ""));
    facts(el("exit-receipt"), [["Fuel debt repaid", (p.repaid_raw / 1e6).toFixed(6) + " test USDC"],
      ["Released SOL sold", (p.sold_lamports / 1e9).toFixed(9)], ["Released SOL kept", (p.retained_lamports / 1e9).toFixed(9)]]);
    el("exit-receipt").hidden = false;
    lastProof = (live ? "Public Devnet simulation" : "Saved Devnet simulation") + " · slot " + p.slot
      + ". The borrower started with zero USDC. The vendor received " + (p.vendor_raw / 1e6).toFixed(4)
      + " test USDC and both debts closed. A second simulation at slot " + p.rollback_slot
      + " rejected a one-lamport sale limit and rolled back the bundle. No transaction was submitted.";
    status.textContent = lastProof;
  } catch {
    status.textContent = "Payoff check unavailable. " + (lastProof || "The saved evidence is available to download.");
  } finally { button.disabled = false; }
}
el("exit-proof").ontoggle = () => {
  if (el("exit-proof").open && !lastProof && !el("exit-simulate").disabled) showProof(false);
};
el("exit-simulate").onclick = () => showProof(true);
