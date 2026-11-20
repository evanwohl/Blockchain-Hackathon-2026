import { el, facts, money, saveFile } from "./common.js";

const node = (tag, text) => {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
};

export function showPumpValue(value, trip, plan) {
  const panel = el("pump-value");
  panel.hidden = !value;
  if (!value) return;
  const quote = money(value.premium_cents);
  const ceiling = money(value.mean_ceiling_cents);
  const detail = document.createElement("details");
  detail.append(node("summary", "How often did it help?"));
  const counts = node("p", "Your fuel money ran short in " + value.short_weeks + " of " + value.weeks
    + " replayed weeks. Oil paid in " + value.paid_weeks + " weeks. In " + value.helped_weeks
    + " of those, the payout covered part or all of a fuel shortfall.");
  detail.append(counts);
  const rows = [["Quoted cost with fees", quote], ["Break-even price across all fills", ceiling],
    ["Break-even price for the hardest 10% of fills", money(value.tail_ceiling_cents)]];
  const estimate = value.estimate;
  if (estimate) rows.push(["Estimated value at this trip’s recent diesel volatility", money(estimate.local_cents)]);
  const figures = document.createElement("dl");
  figures.className = "facts";
  facts(figures, rows);
  detail.append(figures, node("p", "Each ceiling is the most you could spend in whole cents for this entire plan without increasing the corresponding average cash need. Only payouts ready in time to cover fuel count. The replay holds the quote and positions fixed and uses historical oil and regional diesel changes."));
  const single = value.single_quote;
  if (single) {
    const price = cents => cents === null ? "no positive tick" : cents + "¢";
    detail.append(node("p", "Gemini asked " + single.ask + "¢ per $1 of possible payout. At the same quantity, the highest whole-cent ask that broke even was "
      + price(single.mean_limit) + " across all fills and " + price(single.tail_limit) + " for the hardest tenth. Both limits include estimated fees and assume enough contracts at that price."));
  }
  const check = value.validation;
  if (check) {
    const local = check.local, pooled = check.pooled;
    const chosen = (n) => n === 1 ? "1 fill" : n + " fills";
    const versusCash = (net) => net === 0 ? "the same cash as skipping every time"
      : money(Math.abs(net)) + (net > 0 ? " less" : " more") + " than skipping every time";
    detail.append(node("p", "Across " + check.weeks + " later weekly checks, the model used only earlier prices to decide whether to buy. It chose protection for " + chosen(local.buys)
      + " and used " + versusCash(local.net_relief_cents) + " in total. The simple historical average chose protection for " + chosen(pooled.buys)
      + " and used " + versusCash(pooled.net_relief_cents) + ". Buying every time used " + versusCash(check.always_net_cents) + "."));
    const download = node("button", "Download weekly price checks");
    download.type = "button";
    download.className = "secondary";
    download.onclick = () => saveFile("FreightOS-pump-value.json", JSON.stringify({trip, plan, ...value}, null, 2), "application/json");
    detail.append(download);
  }
  let verdict;
  if (value.premium_cents > value.mean_ceiling_cents) {
    verdict = value.premium_cents > value.tail_ceiling_cents
      ? "Keeping the cash cost less, both on average and in the hardest weeks."
      : "Keeping the cash cost less on average. The hedge covered its cost in the hardest weeks.";
  } else {
    verdict = "The hedge covered its cost on average.";
  }
  const answer = node("p", verdict + " Across " + value.weeks + " historical fills, the average break-even price was "
    + ceiling + ". This plan cost " + quote + " including fees.");
  panel.replaceChildren(node("h3", "Was the protection worth its price?"), answer, detail);
}
