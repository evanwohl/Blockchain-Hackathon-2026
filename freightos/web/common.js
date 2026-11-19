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
  const url = URL.createObjectURL(new Blob([JSON.stringify(receipt, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = load + "-settlement.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
