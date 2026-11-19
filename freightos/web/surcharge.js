import { el, facts, money, request } from "./common.js";

export function bindSurcharge(context, dayLabel, useSelection, refresh) {
  const dialog = el("surcharge");
  const form = el("surcharge-form");
  const amount = el("surcharge-amount");
  const day = el("surcharge-day");
  const check = el("check-surcharge");
  const apply = el("apply-surcharge");
  const status = el("surcharge-status");
  let revision = 0;
  let checked;

  function clear() {
    revision++;
    checked = null;
    el("surcharge-result").hidden = true;
    el("surcharge-before").hidden = false;
    status.textContent = "";
    check.disabled = false;
  }

  el("open-surcharge").onclick = () => {
    clear();
    const { state, plan } = context();
    const terms = plan.surcharge;
    const base = state.funding_plans["quick pay"][plan.scenario];
    facts(el("surcharge-base"), [
      ["Fuel due on " + dayLabel(base.calendar.bills[0].day), money(base.bill_cents)],
      ["From the fuel reserve", money(base.payment_cents)],
      ["From early invoice pay", money(base.other_funds_cents)],
      ["Estimated funding cost", money(base.cost_cents)]
    ]);
    amount.value = terms ? (terms.cents / 100).toFixed(2) : "180";
    day.value = terms?.day || "5";
    dialog.showModal();
    amount.focus();
  };
  form.oninput = clear;
  dialog.addEventListener("close", clear);

  form.onsubmit = async event => {
    event.preventDefault();
    clear();
    const token = revision;
    const { state, plan } = context();
    const body = { version: state.record_version, scenario: plan.scenario,
      cents: Math.round(amount.valueAsNumber * 100), day: day.valueAsNumber };
    check.disabled = true;
    status.textContent = "Checking your reserve and invoice offers…";
    try {
      const quote = await request("/api/quote-surcharge", body);
      if (revision !== token) return;
      const fee = quote.cost_cents;
      el("surcharge-outcome").textContent = quote.full_invoices ? "Keep both freight invoices in full" : "Early invoice pay still helps cover fuel";
      facts(el("surcharge-facts"), [
        ["Fuel bill due on " + dayLabel(quote.due), money(quote.bill_cents)],
        ["From the customer for this bill", money(quote.for_fuel_cents)],
        ["From the fuel reserve", money(quote.reserve_cents)],
        ["From early invoice pay", money(quote.bill_cents - quote.for_fuel_cents - quote.reserve_cents)],
        ["Estimated cost with your dates", money(fee)],
        ["Cost if the surcharge clears on " + dayLabel(quote.comparison_day), money(quote.comparison_cost_cents)]
      ]);
      el("surcharge-boundary").textContent = "With your reserve, a surcharge of at least " + money(quote.threshold_cents)
        + " cleared by " + dayLabel(quote.due) + " can keep both invoices on regular terms across all " + quote.budget_count + " fuel estimates.";
      const difference = Math.abs(fee - quote.comparison_cost_cents);
      const timing = quote.day <= quote.due ? "The plan assumes the money clears before you pay for fuel." : "Your surcharge clears after fuel is due.";
      const cheaperDay = fee < quote.comparison_cost_cents ? quote.day : quote.comparison_day;
      status.textContent = difference ? timing + " Clearing on " + dayLabel(cheaperDay) + " saves " + money(difference) + " in estimated fees." : timing;
      el("surcharge-before").hidden = true;
      el("surcharge-result").hidden = false;
      checked = { ...body, id: quote.id };
      el("surcharge-outcome").focus();
    } catch (error) {
      if (revision === token) {
        status.textContent = error.message;
        await reconcile();
      }
    } finally {
      if (revision === token) check.disabled = false;
    }
  };

  async function reconcile() {
    try {
      await refresh();
      const { state } = context();
      if (state.review || state.receipt || state.manual_payment) dialog.close();
    } catch {
      status.textContent = "Couldn’t refresh the bill. Check the connection and try again.";
    }
  }

  apply.onclick = async () => {
    if (!checked || apply.disabled) return;
    const body = checked;
    const token = revision;
    apply.disabled = check.disabled = amount.disabled = day.disabled = true;
    status.textContent = "Updating the bill plan…";
    try {
      const result = await request("/api/apply-surcharge", body);
      if (revision !== token) return;
      await useSelection(result.profile, body.scenario);
      dialog.close();
      el("plan-head").focus();
    } catch (error) {
      if (revision === token) {
        status.textContent = error.message;
        await reconcile();
      }
    } finally {
      apply.disabled = check.disabled = amount.disabled = day.disabled = false;
    }
  };
}
