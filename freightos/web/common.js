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
  group.label = "Other recorded Gemini oil levels";
  for (const [key, plan] of Object.entries(plans)) {
    if (key.startsWith("wti-")) group.append(new Option("Oil at $" + plan.gemini.stress_level + " a barrel", key));
  }
  if (group.children.length) select.append(group);
}

export const usesCredit = profile => profile === "credit line" || profile === "road cash";

export function cashPriority(state, funding, scenario) {
  const field = document.querySelector(".cash-choice");
  field.hidden = !usesCredit(funding);
  if (field.hidden) return funding;
  const selected = field.querySelector(":checked").value;
  const quoted = Object.hasOwn(state.setup_cents, selected);
  for (const input of field.querySelectorAll("input")) {
    const profile = input.value;
    const fee = state.funding_plans[profile][scenario].cost_cents;
    const cash = quoted ? money(state.wallet_usdc_raw / 10000 - state.setup_cents[profile]) : "—";
    const row = input.labels[0];
    row.querySelector("b").textContent = money(fee);
    row.querySelector("strong").textContent = cash;
    input.setAttribute("aria-label", row.querySelector("span").textContent + ", " + money(fee)
      + " estimated fees" + (quoted ? ", " + cash + " left in wallet" : ""));
  }
  const delta = state.setup_cents["credit line"] - state.setup_cents["road cash"];
  const premium = state.funding_plans["road cash"][scenario].cost_cents - state.funding_plans["credit line"][scenario].cost_cents;
  field.querySelector(".cash-note").textContent = !quoted ? "Reserve setup is closed for this bill. You can still compare estimated fees."
    : delta > 0 ? "After setup, keep " + money(delta) + " more available for tolls or repairs for " + money(premium) + " in extra fees."
    : "The plans leave the same wallet balance after setup. Previously deposited funds stay in the reserve.";
  return selected;
}
