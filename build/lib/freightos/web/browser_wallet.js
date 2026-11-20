const connect = document.querySelector("#wallet-connect");
const refresh = document.querySelector("#wallet-refresh");
const disconnect = document.querySelector("#wallet-disconnect");
const status = document.querySelector("#wallet-check");
const account = document.querySelector("#wallet-address");
const preview = document.querySelector("#wallet-preview");
const review = document.querySelector("#wallet-review");
const accept = document.querySelector("#wallet-accept");
const acceptance = document.querySelector("#wallet-acceptance");
let previewVersion = 0;
let wallet;
let address;
let revision = 0;
let receiving;

function clearAcceptance() {
  receiving = undefined;
  accept.hidden = true;
  accept.disabled = false;
  acceptance.textContent = "";
}

async function balances() {
  const current = ++revision;
  clearAcceptance();
  status.textContent = "Checking Solana Devnet…";
  try {
    const response = await fetch("/api/devnet-wallet", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({address}),
    });
    const data = await response.json();
    if (current !== revision) return;
    if (!response.ok || data.address !== address) throw new Error();
    const usdc = BigInt(data.usdc_raw);
    const units = usdc / 1000000n + "." + String(usdc % 1000000n).padStart(6, "0");
    status.textContent = units + " test USDC · " + data.lamports / 1e9 + " test SOL · Devnet slot " + data.slot;
    if (data.usdc_eligible) {
      receiving = data;
      accept.hidden = false;
      acceptance.textContent = "Eligible to receive test USDC. Acceptance is pending.";
    } else {
      acceptance.textContent = "To receive test USDC, this wallet needs an initialized, unfrozen Devnet USDC account.";
    }
  } catch {
    if (current === revision) status.textContent = "Could not check Devnet. Refresh balances to retry.";
  }
}

function selectAccount(key) {
  revision++;
  clearAcceptance();
  address = key?.toString();
  previewVersion++;
  preview.hidden = !address;
  preview.disabled = false;
  review.textContent = "";
  account.textContent = address ? "Connected: " + address : "";
  connect.hidden = Boolean(address);
  refresh.hidden = disconnect.hidden = !address;
  if (address) balances();
  else status.textContent = "Wallet disconnected.";
}

connect.addEventListener("click", async () => {
  const provider = window.phantom?.solana;
  if (!provider?.isPhantom) {
    status.textContent = "Open this page in a browser with the Phantom extension enabled.";
    return;
  }
  if (wallet !== provider) {
    wallet = provider;
    wallet.on("accountChanged", selectAccount);
    wallet.on("disconnect", () => selectAccount(null));
  }
  connect.disabled = true;
  status.textContent = "Approve the connection in Phantom.";
  try {
    const result = await wallet.connect();
    selectAccount(result.publicKey);
  } catch {
    status.textContent = "Connection did not complete. You can try again.";
  } finally {
    connect.disabled = false;
  }
});
refresh.addEventListener("click", balances);
accept.addEventListener("click", async () => {
  if (!receiving || accept.disabled) return;
  const current = revision;
  const checked = receiving;
  accept.disabled = true;
  acceptance.textContent = "Review and sign the payment preference in Phantom.";
  try {
    const signed = await wallet.signMessage(new TextEncoder().encode(checked.acceptance_message), "utf8");
    if (revision !== current) return;
    const signature = btoa(String.fromCharCode(...signed.signature));
    const response = await fetch("/api/devnet-wallet/accept-usdc", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({address: checked.address, signature}),
    });
    const verified = await response.json();
    if (revision !== current) return;
    if (!response.ok) throw new Error(verified.error || "Could not verify acceptance.");
    if (verified.address !== checked.address || verified.usdc_account !== checked.usdc_account ||
        verified.message !== checked.acceptance_message || verified.signature !== signature) {
      throw new Error("Refresh balances and verify acceptance again.");
    }
    accept.hidden = true;
    acceptance.textContent = "Test USDC acceptance verified at Devnet slot " + verified.slot +
      ". The signature confirms your payment preference.";
  } catch (error) {
    if (revision === current) acceptance.textContent = error.message || "Acceptance could not be verified. Try again.";
  } finally {
    if (revision === current) accept.disabled = false;
  }
});
disconnect.addEventListener("click", async () => {
  try {
    await wallet.disconnect();
    selectAccount(null);
  } catch {
    status.textContent = "Could not disconnect. Try again in Phantom.";
  }
});
const views = {reserve: document.querySelector("#reserve"), receipt: document.querySelector("#browser-wallet"), edit: document.querySelector("#edit")};
function selectView() {
  const name = location.hash.slice(1);
  const combined = name.split("?")[0] === "combined";
  const active = combined || name === "credit" ? "edit" : name === "browser-wallet" ? "receipt" : Object.hasOwn(views, name) ? name : "reserve";
  for (const [key, panel] of Object.entries(views)) panel.hidden = key !== active;
  for (const link of document.querySelectorAll(".load-views a")) {
    if (link.hash === "#" + active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (name === "browser-wallet") views.receipt.querySelector("details").open = true;
  document.querySelector("#combined-wallet").hidden = !combined;
  if (combined) {
    document.querySelector("#credit-wallet").open = true;
    document.querySelector("#credit-wallet").scrollIntoView();
  } else if (name === "credit") document.querySelector("#credit").scrollIntoView();
  else window.scrollTo(0, 0);
}
window.addEventListener("hashchange", selectView);
selectView();

preview.addEventListener("click", async () => {
  const version = ++previewVersion;
  preview.disabled = true;
  review.textContent = "Simulating the reserve deposit and approval on Devnet…";
  try {
    const response = await fetch("/api/devnet-approval-preview", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({address}),
    });
    const data = await response.json();
    if (version !== previewVersion) return;
    if (!response.ok) throw new Error(data.error || "Could not prepare the approval.");
    if (data.address !== address) throw new Error("Reconnect the wallet to refresh this preview.");
    review.textContent = "Preview for " + data.load + ": deposit " + data.deposit_raw / 1e6 +
      " test USDC and authorize a later " + data.vendor_payment_raw / 1e6 +
      " test USDC payment to vendor " + data.vendor + ". Unsigned. Valid through Devnet block height " +
      data.last_valid_block_height + ".";
  } catch (error) {
    if (version === previewVersion) review.textContent = error.message;
  } finally {
    if (version === previewVersion) preview.disabled = false;
  }
});
