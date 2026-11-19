import { el, facts, money, request } from "./common.js";

export function bindSurcharge(context, dayLabel, useSelection, refresh) {
  const dialog = el("surcharge");
  const form = el("surcharge-form");
  const amount = el("surcharge-amount");
  const day = el("surcharge-day");
  const offer = el("has-surcharge-offer");
  const regular = el("regular-pay-day");
  const discount = el("customer-fee");
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

  function updateOfferFields() {
    regular.disabled = discount.disabled = el("offer-fields").hidden = !offer.checked;
    el("surcharge-date-label").textContent = offer.checked ? "Early pay day" : "Trip day it clears";
    regular.min = Number.isFinite(day.valueAsNumber) ? day.valueAsNumber + 1 : 2;
    discount.max = amount.value;
  }

  function open(sampleOffer = false) {
    clear();
    const { state, plan } = context();
    const terms = sampleOffer ? null : plan.surcharge;
    const base = state.funding_plans["quick pay"][plan.scenario];
    facts(el("surcharge-base"), [
      ["Fuel due on " + dayLabel(base.calendar.bills[0].day), money(base.bill_cents)],
      ["From the fuel reserve", money(base.payment_cents)],
      ["From early invoice pay", money(base.other_funds_cents)],
      ["Estimated funding cost", money(base.cost_cents)]
    ]);
    amount.value = terms ? (terms.cents / 100).toFixed(2) : "180";
    day.value = terms?.day || "5";
    offer.checked = sampleOffer || Boolean(terms?.regular_day);
    regular.value = terms?.regular_day || "28";
    discount.value = sampleOffer ? "5.00" : terms?.regular_day ? (terms.fee_cents / 100).toFixed(2) : "";
    updateOfferFields();
    dialog.showModal();
    amount.focus();
  }
  el("open-surcharge").onclick = () => open();
  form.oninput = () => {
    clear();
    updateOfferFields();
  };
  dialog.addEventListener("close", clear);

  form.onsubmit = async event => {
    event.preventDefault();
    clear();
    const token = revision;
    const { state, plan } = context();
    const body = { version: state.record_version, scenario: plan.scenario,
      cents: Math.round(amount.valueAsNumber * 100), day: day.valueAsNumber };
    if (offer.checked) {
      body.regular_day = regular.valueAsNumber;
      body.fee_cents = Math.round(discount.valueAsNumber * 100);
    }
    check.disabled = true;
    status.textContent = "Checking your reserve and invoice offers…";
    try {
      const quote = await request("/api/quote-surcharge", body);
      if (revision !== token) return;
      const decision = quote.offer;
      const rows = [
        ["Fuel bill due on " + dayLabel(quote.due), money(quote.bill_cents)],
        ["From the customer for this bill", money(quote.for_fuel_cents)],
        ["From the fuel reserve", money(quote.reserve_cents)],
        ["From early invoice pay", money(quote.bill_cents - quote.for_fuel_cents - quote.reserve_cents)]
      ];
      const heading = el("surcharge-outcome");
      const boundary = el("surcharge-boundary");
      el("offer-limit").hidden = !decision;
      el("surcharge-facts").dataset.wait = String(decision?.take_offer === false);
      if (decision) {
        heading.textContent = decision.take_offer ? "Take the early payment" : "Keep the regular payment date";
        rows.push(["Total cost with the early offer", money(decision.early_cost_cents)],
          ["Total cost if you wait", money(decision.wait_cost_cents)]);
        el("offer-limit-value").textContent = decision.highest_fee_cents === null ? "None" : money(decision.highest_fee_cents);
        boundary.textContent = decision.highest_fee_cents === null
          ? "Waiting stays competitive in at least one estimate even if early payment is free."
          : "This limit leaves you ahead after fees in every tested fuel budget, using the same invoice terms throughout.";
        status.textContent = decision.take_offer
          ? "Arrange " + money(quote.cents - decision.fee_cents) + " on " + dayLabel(decision.early_day) + " with the customer. After the fee, you save at least "
            + money(decision.least_saved_cents) + " across the " + quote.budget_count + " fuel estimates."
          : "Collect the full " + money(quote.cents) + " on " + dayLabel(decision.regular_day)
            + ". The offered fee leaves at least one fuel estimate as expensive as waiting.";
        apply.textContent = decision.take_offer ? "Use the early-payment plan" : "Use the regular payment date";
      } else {
        heading.textContent = quote.full_invoices ? "Keep both freight invoices in full" : "Early invoice pay still helps cover fuel";
        rows.push(["Estimated cost with your dates", money(quote.cost_cents)],
          ["Cost if the surcharge clears on " + dayLabel(quote.comparison_day), money(quote.comparison_cost_cents)]);
        boundary.textContent = "With your reserve, a surcharge of at least " + money(quote.threshold_cents)
          + " cleared by " + dayLabel(quote.due) + " can keep both invoices on regular terms across all " + quote.budget_count + " fuel estimates.";
        const difference = Math.abs(quote.cost_cents - quote.comparison_cost_cents);
        const timing = quote.day <= quote.due ? "The plan assumes the money clears before you pay for fuel." : "Your surcharge clears after fuel is due.";
        const cheaperDay = quote.cost_cents < quote.comparison_cost_cents ? quote.day : quote.comparison_day;
        status.textContent = difference ? timing + " Clearing on " + dayLabel(cheaperDay) + " saves " + money(difference) + " in estimated fees." : timing;
        apply.textContent = "Use this plan";
      }
      facts(el("surcharge-facts"), rows);
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
    apply.disabled = check.disabled = amount.disabled = day.disabled = offer.disabled = regular.disabled = discount.disabled = true;
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
      apply.disabled = check.disabled = amount.disabled = day.disabled = offer.disabled = false;
      updateOfferFields();
    }
  };
  return open;
}
