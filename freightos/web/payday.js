import { facts, money } from "./common.js";

export function cashSchedule(run, credit) {
  const weekly = run.recent.map(path => Math.max(...path.need_cents.map((need, i) =>
    Math.ceil(Math.max(0, need - credit) / (i + 1))))).sort((a, b) => a - b);
  const rank = Math.min(weekly.length, Math.ceil(0.9 * (weekly.length + 1)));
  return {upfront: Math.max(0, run.target_cents - credit), weekly: weekly[rank - 1]};
}

function bridge(bounds, weekly) {
  return Math.min(...bounds.map(row => Math.max(0, ...row.map((need, i) => need - (i + 1) * weekly))));
}

export function budgetPlan(run, weekly) {
  const request = bridge(run.bounds, weekly);
  let best = null;
  for (const bounds of run.bounds) {
    let cumulative = 0, cost = 0;
    const schedule = [];
    for (const [i, need] of bounds.entries()) {
      const due = (i + 1) * weekly;
      const next = Math.max(cumulative, due, need);
      schedule.push(next - cumulative);
      cost += next - due;
      cumulative = next;
    }
    if (cumulative === run.stops * weekly && (!best || cost < best.cost)) best = {cost, schedule};
  }
  const history = run.records.map(row => {
    const draw = bridge(row.bounds, weekly);
    const needed = Math.max(0, ...row.need_cents.map((v, i) => v - (i + 1) * weekly));
    return {draw, short: needed > draw};
  });
  return {weekly, request, schedule: best?.schedule, history};
}

export function showSchedule(panel, run, credit, entry = null) {
  const cash = cashSchedule(run, credit);
  if (entry == null) {
    facts(panel, [["Keep extra before the first fill", money(cash.upfront)],
      ["Or add before each of " + run.stops + " fills", money(cash.weekly)]]);
    return;
  }
  const setup = run.premium_cents + entry;
  facts(panel, [["All at once · extra cash at setup", money(cash.upfront + setup)],
    ["Or weekly · extra cash at setup", money(cash.weekly + setup)],
    ["Then before each remaining fill", money(cash.weekly)]]);
}

export function runEvidence(run) {
  if (!run.records.length) return "There is too little later history to check this schedule independently.";
  return "Across " + run.records.length + " separate historical runs, this buffer ran short " + run.multi.misses
    + " times. Keeping the one-stop buffer just once ran short " + run.single.misses + " times. The two methods set aside "
    + money(run.multi.mean_cents) + " and " + money(run.single.mean_cents) + " on average. The largest gap left by the multi-stop estimate was "
    + money(run.multi.worst_uncovered_cents) + ".";
}

export function creditUse(run, capacity) {
  let own = 0, withLoan = 0;
  for (const row of run.records) {
    const credit = Math.min(capacity, row.target_cents);
    if (row.gap_cents <= row.target_cents - credit) own++;
    else if (row.gap_cents <= row.target_cents) withLoan++;
  }
  const count = run.records.length;
  return "Across " + count + " later stretches with each historical lump-sum budget, planned cash alone covered " + own
    + ". Adding credit covered " + withLoan + " more. " + (count - own - withLoan)
    + " still ran short. This assumes the same credit ceiling for each stretch. An upfront loan incurs borrowing costs even when the fuel money goes unused.";
}
