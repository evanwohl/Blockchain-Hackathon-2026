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
