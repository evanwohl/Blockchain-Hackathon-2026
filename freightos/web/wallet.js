import { el, facts, money, request, saveFile, tokenUnits } from "./common.js";

let current;
let quote;
let sequence = 0;
const dollars = raw => raw > 0 && raw < 10000 ? "Less than 1¢"
  : (raw % 10000 ? "≈ " : "") + money(raw / 10000);

export function walletState(state, profile, busy) {
  current = { state, profile };
  const shares = state.reserve_shares_raw > 0;
  el("wallet-strip").hidden = !(state.last_return || (state.deposit && shares && !state.review));
  el("open-return").hidden = !shares || Boolean(state.review);
  el("last-return").hidden = !state.last_return;
  el("open-return").disabled = el("last-return").disabled = busy;
  el("wallet-summary").textContent = state.last_return && !shares
    ? "Unused fuel money is back in your wallet. Your balance is " + dollars(state.wallet_usdc_raw) + "."
    : state.receipt || state.manual_payment ? "This bill needs no more reserve funds. Check what you can move back to your wallet."
    : "Check what you can take out while keeping enough for the fuel budgets in this plan.";
}

function show(title, copy, label, amount, rows, mode) {
  el("wallet-head").textContent = title;
  el("wallet-copy").textContent = copy;
  el("wallet-outcome").hidden = amount == null;
  el("wallet-amount").textContent = amount == null ? "" : dollars(amount);
  el("wallet-label").textContent = label;
  facts(el("wallet-facts"), rows);
  el("confirm-return").hidden = mode !== "confirm";
  el("retry-return").hidden = mode !== "retry";
  el("wallet-tools").hidden = mode !== "receipt";
  el("wallet-status").textContent = "";
}

function showReceipt() {
  const receipt = current.state.last_return;
  show("Your wallet received the money", "Your fuel money is back in your demo wallet. The amount owed to the fuel company stays the same.",
    "Returned from the reserve", receipt.quote.return_usdc_raw, [
      ["Wallet after the return", dollars(receipt.wallet_after_raw)],
      ["Exact amount received", tokenUnits(receipt.quote.return_usdc_raw) + " USDC"]
    ], "receipt");
}

async function loadQuote() {
  const attempt = ++sequence;
  quote = null;
  show("Check unused fuel money", "Checking the reserve and your current bill…", "", null, [], "");
  try {
    const { state, profile } = current;
    const result = await request("/api/return-quote", { version: state.record_version, profile });
    if (attempt !== sequence) return;
    quote = result;
    const canReturn = result.return_usdc_raw > 0;
    const protectedAmount = money(result.protected_cents);
    show(canReturn ? "Move unused money back" : "Keep this money set aside",
      result.protected_cents ? "Your plan needs " + protectedAmount + " available across its tested fuel budgets."
        : "Your current bill needs no more money from this reserve.",
      "Available to return", result.return_usdc_raw, [
        ["Wallet after this return", dollars(result.wallet_before_raw + result.return_usdc_raw)],
        ["Needed in the reserve", protectedAmount]
      ], canReturn ? "confirm" : "");
    if (!canReturn) el("wallet-status").textContent = "There’s no spendable cent above the amount needed for this plan.";
  } catch (error) {
    if (attempt === sequence) show("Check your reserve again", error.message, "", null, [], "retry");
  }
}

export function bindWallet(run, refresh) {
  el("open-return").onclick = () => { el("wallet-dialog").showModal(); loadQuote(); };
  el("last-return").onclick = () => { showReceipt(); el("wallet-dialog").showModal(); };
  el("retry-return").onclick = async () => {
    try { await refresh(); await loadQuote(); }
    catch { el("wallet-status").textContent = "Could not refresh the wallet. Check again."; }
  };
  el("wallet-dialog").onclose = () => { sequence++; quote = null; };
  el("confirm-return").onclick = async () => {
    if (!quote) return;
    const approved = quote;
    quote = null;
    el("confirm-return").disabled = true;
    el("wallet-status").textContent = "Moving the money to your wallet…";
    await run("Returning funds…", () => request("/api/return-reserve", {
      id: approved.id, profile: approved.profile, version: approved.version
    }), el("wallet-status"));
    el("confirm-return").disabled = false;
    if (current.state.last_return?.quote.version === approved.version && current.state.last_return?.quote.profile === approved.profile) {
      showReceipt();
      el("wallet-head").focus();
    } else {
      el("confirm-return").hidden = true;
      el("retry-return").hidden = false;
    }
  };
  for (const id of ["verify-return", "download-return"]) {
    el(id).onclick = async () => {
      const receipt = current.state.last_return;
      el(id).disabled = true;
      try {
        const proof = await request(id === "verify-return" ? "/api/verify-return" : "/api/return-receipt", { signature: receipt.signature });
        if (id === "verify-return") el("wallet-status").textContent = "Signed transfer verified. Your wallet received " + tokenUnits(proof.returned_usdc_raw) + " USDC."
          + (proof.protected_value_usdc_raw ? " The reserve still covered " + dollars(proof.protected_value_usdc_raw) + " immediately afterward." : "");
        else saveFile(receipt.quote.load + "-wallet-return.json", JSON.stringify(proof, null, 2), "application/json");
      } catch (error) { el("wallet-status").textContent = error.message; }
      finally { el(id).disabled = false; }
    };
  }
}
