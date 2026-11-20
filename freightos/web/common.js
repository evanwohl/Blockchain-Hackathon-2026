export const el = id => document.getElementById(id);

const dollars = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
export const money = cents => dollars.format(cents / 100);
export const tokenUnits = raw => (raw / 1e6).toLocaleString("en-US", { maximumFractionDigits: 6 });

export async function request(path, body, signal) {
  signal ??= AbortSignal.timeout(20000);
  const options = body === undefined ? { signal } : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal
  };
  const response = await fetch(path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || "The app could not complete this request. Try again.");
  }
  return response.json();
}

export function facts(container, rows) {
  container.replaceChildren(...rows.map(([label, value]) => {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    row.append(term, detail);
    return row;
  }));
}

export function downloadReceipt(load, receipt) {
  saveFile(load + "-settlement.json", JSON.stringify(receipt, null, 2), "application/json");
}

export function saveFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function oilOptions(select, plans) {
  if (select.querySelector("optgroup")) return;
  const group = document.createElement("optgroup");
  group.label = "Recorded Gemini oil estimates";
  for (const [key, plan] of Object.entries(plans)) {
    if (plan.gemini.settled_reference) group.append(new Option("$" + plan.gemini.stress_level + " oil from the reported settlement", key));
    else if (key.startsWith("wti-")) group.append(new Option("Oil at $" + plan.gemini.stress_level + " a barrel", key));
  }
  if (group.children.length) select.append(group);
}

export const usesCredit = profile => profile === "credit line" || profile === "road cash";

export function cashPriority(state, funding, scenario, late = false) {
  const field = document.querySelector(".cash-choice");
  field.hidden = !usesCredit(funding);
  if (field.hidden) return funding;
  const resolve = key => late ? state.pairing_values[key][scenario].invoice_delay.alternative : key;
  const selected = resolve(field.querySelector(":checked").value);
  const quoted = Object.hasOwn(state.setup_cents, selected);
  for (const input of field.querySelectorAll("input")) {
    const profile = resolve(input.value);
    const fee = state.funding_plans[profile][scenario].cost_cents;
    const available = quoted && state.setup_cents[profile] != null;
    const cash = available ? money(state.wallet_usdc_raw / 10000 - state.setup_cents[profile]) : "—";
    const row = input.labels[0];
    row.querySelector("b").textContent = money(fee);
    row.querySelector("strong").textContent = cash;
    input.setAttribute("aria-label", row.querySelector("span").textContent + ", " + money(fee)
      + " estimated costs" + (available ? ", " + cash + " left in wallet" : ""));
  }
  const credit = resolve("credit line"), road = resolve("road cash");
  const delta = state.setup_cents[credit] - state.setup_cents[road];
  const premium = state.funding_plans[road][scenario].cost_cents - state.funding_plans[credit][scenario].cost_cents;
  field.querySelector(".cash-note").textContent = !quoted ? "Reserve setup is closed for this bill. You can still compare estimated costs."
    : state.setup_cents[credit] === null || state.setup_cents[road] === null ? "A dash means the reserve is unavailable for that option."
    : delta > 0 ? "After setup, keep " + money(delta) + " more available for tolls or repairs for " + money(premium) + " in extra costs."
    : "The plans leave the same wallet balance after setup. In the bill, check unused money to see what you can move back to your wallet.";
  return selected;
}

export async function verifySample(load, button, result, signal) {
  button.disabled = true;
  result.textContent = "Checking the signed transfer against the saved Solana state…";
  try {
    const proof = await request("/api/trial/" + encodeURIComponent(load) + "/verify", undefined, signal);
    if (!signal?.aborted) result.textContent = "Verified. The fuel company received " + money(proof.vendor_received_usdc_raw / 10000)
      + " in demo dollars on " + load + ". The replay checked " + proof.verified_signatures + " signed transactions.";
  } catch {
    if (!signal?.aborted) result.textContent = "The payment could not be verified. Try checking it again.";
  } finally {
    if (!signal?.aborted) button.disabled = false;
  }
}

export function bindTabs(list) {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  function select(id) {
    for (const tab of tabs) {
      const active = tab.id === id;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      el(tab.getAttribute("aria-controls")).hidden = !active;
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.onclick = () => select(tab.id);
    tab.onkeydown = event => {
      const next = {ArrowLeft: (index + tabs.length - 1) % tabs.length, ArrowRight: (index + 1) % tabs.length,
        Home: 0, End: tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault();
      select(tabs[next].id);
      tabs[next].focus();
    };
  }
  return select;
}
