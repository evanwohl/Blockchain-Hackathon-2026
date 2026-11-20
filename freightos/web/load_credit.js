import {el, facts, money, request} from "./common.js";

const sol = lamports => (lamports / 1e9).toLocaleString("en-US", {maximumFractionDigits: 9}) + " test SOL";
const usdc = raw => (raw / 1e6).toFixed(6) + " test USDC";
const chain = "solana:devnet";
const explorer = signature => "https://explorer.solana.com/tx/" + encodeURIComponent(signature) + "?cluster=devnet";
const write = (id, text) => { el(id).textContent = text; };

async function showCycle(live) {
  const button = el("cycle-verify");
  button.disabled = true;
  write("cycle-status", live ? "Checking both finalized transactions on Devnet…" : "Checking saved evidence…");
  try {
    const p = await request("/api/settled-credit" + (live ? "/verify" : ""));
    const steps = [["Collateral pledged", sol(p.collateral_lamports)],
      ["Fuel vendor received", usdc(p.vendor_raw)], ["Borrower repaid", usdc(p.repaid_raw)]];
    el("cycle-steps").replaceChildren(...steps.map(([label, amount]) => {
      const row = document.createElement("li"), value = document.createElement("strong");
      value.textContent = amount;
      row.append(label, value);
      return row;
    }));
    write("cycle-outcome", "Debt closed · " + sol(p.returned_lamports) + " returned to the driver.");
    el("cycle-open").href = explorer(p.borrow_signature);
    el("cycle-close").href = explorer(p.repay_signature);
    el("cycle-links").hidden = false;
    write("cycle-status", (live ? "Verified on public Devnet" : "Verified from saved Devnet transactions") + " · $7 sample fuel gap");
    write("cycle-cost", "Interest paid over " + p.elapsed_seconds + " seconds was " + usdc(p.interest_raw)
      + ". The two network fees totaled " + sol(p.network_lamports) + ". Kamino returned " + sol(p.obligation_rent_lamports)
      + " of account rent. " + sol(p.metadata_rent_lamports) + " stayed in the borrower profile.");
  } catch {
    write("cycle-status", live && !el("cycle-links").hidden ? "Live check unavailable. The saved, verified receipt is shown below." : "Could not verify the receipt. Try again.");
  } finally { button.disabled = false; }
}

function signatureText(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length !== 64) throw new Error("Check the wallet history for the submitted signature.");
  const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  let value = 0n, output = "", zeros = 0;
  for (const byte of bytes) value = value * 256n + BigInt(byte);
  while (value) {
    output = alphabet[Number(value % 58n)] + output;
    value /= 58n;
  }
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
  return "1".repeat(zeros) + output;
}

export function setupLoadCredit(inputs, form) {
  const providers = [], select = el("credit-provider"), send = el("credit-send"), sig = el("credit-sig");
  const cap = el("credit-cap"), price = el("credit-price"), connect = el("credit-connect"), payoff = el("credit-payoff");
  let wallet, account, pending, unsubscribe, revision = 0, signing = false;
  const storageKey = address => "freightos:devnet:loan:" + address;
  const status = text => write("credit-wallet-status", text);

  function invalidate() {
    revision++;
    pending = undefined;
    send.hidden = true;
    el("credit-terms").hidden = true;
    el("combined-review").replaceChildren();
    write("credit-msg", "");
    status("");
  }
  function setAccount(next) {
    invalidate();
    account = next;
    sig.value = "";
    if (account) {
      try { sig.value = localStorage.getItem(storageKey(account.address)) || ""; } catch {}
    }
    write("credit-account", account ? "Connected on Devnet · " + account.address : "");
    payoff.disabled = !account;
    el("credit-explorer").hidden = true;
  }
  function chooseProvider() {
    unsubscribe?.();
    wallet = providers[Number(select.value)];
    setAccount(undefined);
    connect.disabled = !wallet;
    unsubscribe = wallet?.features["standard:events"]?.on("change", changes => {
      if (changes.accounts && account) setAccount(changes.accounts.find(a => a.address === account.address && a.chains.includes(chain)));
    });
  }
  function register(...wallets) {
    for (const item of wallets) {
      if (providers.includes(item) || !item.chains?.includes(chain) || !item.features["standard:connect"] || !item.features["solana:signAndSendTransaction"]) continue;
      if (!providers.length) select.replaceChildren();
      providers.push(item);
      select.add(new Option(item.name, String(providers.length - 1)));
      if (providers.length === 1) chooseProvider();
    }
    return () => {};
  }
  select.add(new Option("Open in a Solana wallet browser", ""));
  chooseProvider();
  const api = Object.freeze({register});
  window.addEventListener("wallet-standard:register-wallet", e => e.detail(api));
  window.dispatchEvent(new CustomEvent("wallet-standard:app-ready", {detail: api}));
  select.onchange = chooseProvider;
  connect.onclick = async () => {
    const selected = wallet;
    connect.disabled = true;
    try {
      const {accounts} = await selected.features["standard:connect"].connect();
      if (selected !== wallet) return;
      const next = accounts.find(a => a.chains.includes(chain) && a.features.includes("solana:signAndSendTransaction"));
      if (!next) throw new Error("Enable a Devnet account that can send transactions.");
      setAccount(next);
      status("Check this load’s terms to prepare a vendor payment.");
    } catch (err) { if (wallet === selected) status(err.message); }
    finally { connect.disabled = !wallet; }
  };
  el("cycle-verify").onclick = () => showCycle(true);
  showCycle(false);

  form.addEventListener("input", invalidate);
  window.addEventListener("hashchange", invalidate);
  cap.oninput = invalidate;
  sig.oninput = invalidate;
  let savedFields;
  el("credit-example").onclick = () => {
    if (savedFields) {
      for (const [field, value, checked] of savedFields) {
        field.value = value;
        if (field.type === "checkbox") field.checked = checked;
      }
      savedFields = undefined;
      el("credit-example").textContent = "Use the $7 example in my load";
      form.dispatchEvent(new Event("input", {bubbles: true}));
      write("credit-msg", "Your previous load is restored.");
      return;
    }
    savedFields = Array.from(form.elements).filter(field => field.name).map(field => [field, field.value, field.checked]);
    el("credit-example").textContent = "Restore my previous load";
    form.reset();
    form.elements.opening_cents.value = "838";
    form.elements.surcharge_cents.value = "0";
    form.dispatchEvent(new Event("input", {bubbles: true}));
    write("credit-msg", "$745 fuel bill · $838 available · $100 kept for the road. The remaining fuel gap is $7.");
  };
  function review(quote, kind) {
    if (!quote.transaction || !account || quote.address !== account.address) return;
    pending = {quote, kind, wallet, account};
    send.textContent = kind === "combined" ? "Approve both sources and pay the vendor" : kind === "borrow" ? "Approve loan and pay fuel vendor" : "Approve payoff and return collateral";
    send.hidden = false;
    el("credit-wallet").open = true;
  }
  price.onclick = async () => {
    invalidate();
    if (!form.reportValidity() || !cap.reportValidity() || !cap.value) return;
    const version = revision;
    write("credit-msg", "Simulating the fuel payment and checking collateral on Devnet…");
    try {
      const q = await request("/api/load-credit", {load: inputs(), address: account?.address, pledge_limit_lamports: Math.round(cap.valueAsNumber * 1e9)});
      if (revision !== version) return;
      facts(el("credit-figures"), [["Fuel shortfall", money(q.gap_cents)], ["Sent to the vendor", usdc(q.advance_raw)],
        ["Your collateral", sol(q.collateral_lamports)], ["Account setup", sol(q.setup_lamports)], ["Opening network fee", sol(q.network_lamports)],
        ["Estimated payoff in " + q.days + " days", money(q.repay_cents) + " at sample scale"], ["Payee’s test USDC account", q.vendor]]);
      write("credit-downside", "The pledge is sized for a 40% SOL price drop. Estimated liquidation price: " + money(q.liquidation_price_cents)
        + " per SOL. A deeper drop could cost you collateral. Borrow rates can change.");
      el("credit-terms").hidden = false;
      write("credit-msg", account ? "Review the amounts and approve in your wallet when ready." : "Read-only quote using a reference wallet. Connect your own Devnet wallet to check your terms.");
      review(q, "borrow");
    } catch (err) { if (revision === version) write("credit-msg", err.message); }
  };
  el("combined-prepare").onclick = async () => {
    invalidate();
    const version = revision;
    if (!account) { status("Connect the demo carrier wallet to prepare this bill."); return; }
    if (!wallet.features["solana:signAndSendTransaction"].supportedTransactionVersions?.includes(1)) {
      status("This wallet does not report support for Solana v1 transactions. You can inspect the simulation without signing."); return;
    }
    const query = new URLSearchParams(location.hash.split("?")[1]);
    const days = Number(query.get("days") || 23), drop = Number(query.get("drop") || 40);
    status("Checking both funding sources on Devnet…");
    try {
      const q = await request("/api/atomic-fuel/prepare", {address: account.address, source: "live", days, drop,
        protect_payday: query.get("late") === "1", offer_fee_cents: Number(query.get("fee") ?? 1000), other_cents: Number(query.get("other") ?? 0)}, AbortSignal.timeout(90000));
      if (revision !== version || q.borrower !== account.address) return;
      if (q.offer.choice !== "loan") {
        status(q.offer.choice === "early" ? "At the current quote, use the early freight payment. Confirm its date with your customer." : "These terms leave a shortfall. Return to the comparison before arranging funds."); return;
      }
      facts(el("combined-review"), [["Vendor receives", usdc(q.vendor_raw)], ["From reserve", usdc(q.reserve_raw)],
        ["New debt principal", usdc(q.borrow_raw)], ["Test SOL pledged", sol(q.pledge_lamports)],
        ["Loan account setup", sol(q.setup_lamports)], ["Network fee", sol(q.fee_lamports)],
        ["Projected repayment in " + days + " days", money(q.repay_cents) + " at sample scale"], ...(q.payday_stress.protected ? [["Repay in the late, double-rate case", money(q.payday_stress.repay_cents) + " at sample scale"]] : []), ["Vendor account", q.vendor]]);
      status("Both sources must clear together. Review the amounts before approving.");
      review({...q, address: q.borrower}, "combined");
    } catch (err) { if (revision === version) status(err.message); }
  };
  payoff.onclick = async () => {
    invalidate();
    const version = revision;
    if (!account || !sig.value.trim()) { status("Connect the borrowing wallet and enter its original transaction signature."); return; }
    status("Checking the signed vendor payment and current debt…");
    try {
      const q = await request("/api/load-credit/payoff", {address: account.address, signature: sig.value.trim()});
      if (revision !== version) return;
      if (q.closed) { status("This borrowing account is closed. No payoff is due."); return; }
      status("Estimated payoff " + usdc(q.repay_raw) + ", maximum " + usdc(q.max_repay_raw) + ". Return " + sol(q.returned_lamports) + " in collateral. Network fee " + sol(q.network_lamports) + ".");
      review(q, "repay");
    } catch (err) { if (revision === version) status(err.message); }
  };
  send.onclick = async () => {
    const action = pending;
    if (!action || signing) return;
    pending = undefined;
    send.hidden = true;
    const signer = action.wallet.accounts.find(a => a.address === action.account.address && a.chains.includes(chain));
    if (!signer || wallet !== action.wallet) { invalidate(); status("Wallet changed. Check the terms again."); return; }
    signing = true;
    const controls = [form, cap, select, price, connect, payoff, sig, el("credit-example"), el("combined-prepare")];
    controls.forEach(node => { node.inert = true; });
    status("Review the Devnet transaction in your wallet.");
    try {
      const [result] = await action.wallet.features["solana:signAndSendTransaction"].signAndSendTransaction({
        account: signer, chain, transaction: Uint8Array.from(atob(action.quote.transaction), c => c.charCodeAt(0)),
        options: {preflightCommitment: "confirmed", skipPreflight: false}
      });
      const signature = signatureText(result.signature);
      if (action.kind !== "repay") {
        try { localStorage.setItem(storageKey(signer.address), signature); } catch {}
      }
      if (account?.address !== signer.address) return;
      if (action.kind !== "repay") sig.value = signature;
      el("credit-explorer").href = explorer(signature);
      el("credit-explorer").hidden = false;
      status("Submitted on Devnet. Open the transaction to check confirmation, then use Check payoff to check the debt.");
    } catch (err) {
      status("Wallet response: " + err.message + " Check your wallet history before requesting new terms.");
    } finally {
      signing = false;
      controls.forEach(node => { node.inert = false; });
    }
  };
}
