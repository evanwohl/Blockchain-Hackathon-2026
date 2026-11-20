import assert from "node:assert/strict";
import test from "node:test";
import { budgetPlan, leastFlex } from "../freightos/web/payday.js";

function schedules(weekly, stops) {
  const results = [];
  function choose(prior, left) {
    if (prior.length === stops - 1) {
      results.push([...prior, left]);
      return;
    }
    for (let cents = 0; cents <= left; cents++) choose([...prior, cents], left - cents);
  }
  choose([], weekly * stops);
  return results.map(cash => {
    let sum = 0;
    const leads = cash.map((v, i) => (sum += v) - (i + 1) * weekly);
    return {cash, leads, cap: Math.max(...leads), time: leads.reduce((a, b) => a + b, 0)};
  }).filter(s => s.leads.every(v => v >= 0));
}

test("minimum loan, earliest-cash limit and cash timing match direct enumeration", () => {
  for (const stops of [2, 3, 4]) {
    for (let seed = 1; seed <= 12; seed++) {
      const weekly = seed % 5;
      const paths = Array.from({length: 26}, (_, k) => Array.from({length: stops},
        (_, j) => (seed * k + 7 * j + k * j) % 19));
      const bounds = paths.map((_, k) => Array.from({length: stops}, (_, i) =>
        Math.max(...paths.filter((_, j) => j !== k).map(row => row[i]))));
      const run = {stops, bounds, records: [{first: "test", bounds, need_cents: paths[0]}]};
      const eligible = schedules(weekly, stops).map(schedule => {
        const needs = paths.map(row => Math.max(0, ...row.map((v, i) =>
          v - (i + 1) * weekly - schedule.leads[i]))).sort((a, b) => a - b);
        return {...schedule, loan: needs[24]};
      });
      for (const flex of [0, 1, 3, 8, 20]) {
        const options = eligible.filter(row => row.cap <= flex);
        const minLoan = Math.min(...options.map(row => row.loan));
        const minTime = Math.min(...options.filter(row => row.loan === minLoan).map(row => row.time));
        const result = budgetPlan(run, weekly, flex);
        const selected = options.find(row => row.cash.every((v, i) => v === result.contributions[i]));
        assert.ok(selected);
        assert.equal(result.request, minLoan);
        assert.equal(selected.loan, minLoan);
        assert.equal(selected.time, minTime);
        assert.equal(result.moved, selected.cap);
        assert.deepEqual(result.history[0].contributions, result.contributions);
        const actualGap = Math.max(0, ...paths[0].map((v, i) => v - (i + 1) * weekly - selected.leads[i] - minLoan));
        assert.equal(result.history[0].deficit, actualGap);
      }
      for (let loan = 0; loan < 19; loan++) {
        const options = eligible.filter(row => row.loan <= loan);
        assert.equal(leastFlex(bounds, weekly, loan), options.length ? Math.min(...options.map(row => row.cap)) : null);
      }
    }
  }
});
