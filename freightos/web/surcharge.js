import { el, facts, money, request } from "./common.js";

export function bindSurcharge(context, dayLabel, useSelection, refresh) {
  const dialog = el("surcharge");
  const form = el("surcharge-form");
  const amount = el("surcharge-amount");
  const day = el("surcharge-day");
  const offer = el("has-surcharge-offer");
  const partial = el("allow-partial");
  const regular = el("regular-pay-day");
  const discount = el("customer-fee");
  const road = el("road-cash");
  const budget = el("reserve-limit");
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
    el("offer-sources").open = el("whole-comparison").open = false;
  }

  function updateOfferFields() {
    el("road-cash-field").querySelector("summary").textContent = road.valueAsNumber > 0 && road.validity.valid
      ? "Keep " + money(Math.round(road.valueAsNumber * 100)) + " for the road" : "Keep cash for tolls and repairs";
    el("reserve-limit-field").querySelector("summary").textContent = budget.value !== "" && budget.validity.valid
      ? "Fuel reserve limit · " + money(Math.round(budget.valueAsNumber * 100)) : "Set a fuel reserve limit";
    partial.disabled = el("partial-field").hidden = !offer.checked;
    regular.disabled = discount.disabled = el("offer-fields").hidden = !offer.checked;
    el("surcharge-date-label").textContent = offer.checked ? "Early pay day" : "Trip day it clears";
    el("customer-fee-label").textContent = partial.checked ? "Fee on the full amount" : "Fee they keep";
    regular.min = Number.isFinite(day.valueAsNumber) ? day.valueAsNumber + 1 : 2;
    discount.max = amount.value;
  }

  function open(sampleOffer = false, preset = new URLSearchParams()) {
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
    partial.checked = Boolean(terms?.advance_cents) || (sampleOffer && preset.get("partial") === "1");
    regular.value = terms?.regular_day || "28";
    discount.value = sampleOffer ? "5.00" : terms?.regular_day ? ((terms.full_fee_cents ?? terms.fee_cents) / 100).toFixed(2) : "";
    const presetCents = name => {
      const value = preset.get(name);
      return sampleOffer && /^\d{1,12}$/.test(value || "") ? Number(value) : null;
    };
    const face = presetCents("surcharge_cents");
    if (face > 0) amount.value = (face / 100).toFixed(2);
    const fee = presetCents("offer_fee_cents");
    if (fee !== null) discount.value = (fee / 100).toFixed(2);
    const limit = sampleOffer ? presetCents("reserve_limit_cents") : terms ? state.surcharge_limit_cents : null;
    const floor = sampleOffer ? presetCents("road_cash_cents") : terms ? plan.road_cash_cents : null;
    road.value = floor == null ? "" : (floor / 100).toFixed(2);
    el("road-cash-field").open = road.value !== "";
    budget.value = limit == null ? "" : (limit / 100).toFixed(2);
    el("reserve-limit-field").open = budget.value !== "";
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
    if (road.value !== "") body.road_cash_cents = Math.round(road.valueAsNumber * 100);
    if (budget.value !== "") body.reserve_limit_cents = Math.round(budget.valueAsNumber * 100);
    if (offer.checked) {
      if (partial.checked) body.allow_partial = true;
      body.regular_day = regular.valueAsNumber;
      body.fee_cents = Math.round(discount.valueAsNumber * 100);
    }
    check.disabled = true;
    status.textContent = "Checking your reserve and invoice offers…";
    try {
      const quote = await request("/api/quote-surcharge", body);
      if (revision !== token) return;
      const decision = quote.offer;
      const split = decision?.partial;
      el("partial-dates").hidden = !split;
      const showWhole = split?.setup_difference_cents > 0 && quote.road_cash_cents > 0;
      el("whole-comparison").hidden = !showWhole;
      if (showWhole) {
        el("whole-tradeoff").textContent = (split.baseline_early ? "Taking the full advance" : "Waiting for regular payment")
          + " leaves " + money(split.wallet_after_cents + split.setup_difference_cents) + " in your wallet and costs "
          + money(split.baseline_cost_cents - quote.cost_cents) + " more in fees on this bill.";
      }
      const sources = [
        ["Fuel bill due on " + dayLabel(quote.due), money(quote.bill_cents)],
        ["From the customer for this bill", money(quote.for_fuel_cents)],
        ["From the fuel reserve", money(quote.reserve_cents)],
        ["From early invoice pay", money(quote.bill_cents - quote.for_fuel_cents - quote.reserve_cents)]
      ];
      const rows = decision ? [] : sources;
      const heading = el("surcharge-outcome");
      const boundary = el("surcharge-boundary");
      el("public-offer-example").hidden = !decision;
      el("offer-limit").hidden = !decision;
      const alternative = decision?.larger_budget;
      el("budget-option").hidden = !alternative;
      if (alternative) {
        el("compare-budget").value = String(alternative.limit_cents);
        el("compare-budget").textContent = "Check a " + money(alternative.limit_cents) + " reserve limit";
        const setup = alternative.extra_setup_cents > 0 ? "This takes " + money(alternative.extra_setup_cents) + " more from your wallet."
          : "Your current setup budget covers this option.";
        const lead = alternative.minimum_to_beat_current ? money(alternative.limit_cents) + " is the lowest reserve limit that beats your current waiting plan in every estimate. " : "You could allow " + money(alternative.limit_cents) + " from your reserve. ";
        el("budget-tradeoff").textContent = lead + "Estimated fees are " + money(alternative.early_cost_cents)
          + " for early pay or " + money(alternative.wait_cost_cents) + " for waiting. " + setup;
      }
      el("offer-sources").hidden = !decision;
      if (decision) {
        el("offer-limit-label").textContent = split ? "Ask the customer to advance" : "Highest fee worth paying";
        facts(el("offer-source-rows"), sources);
        el("offer-sources").querySelector("summary").textContent = "How the " + money(quote.bill_cents) + " fuel bill gets paid";
      }
      if (split) {
        heading.textContent = "Take only part early";
        el("offer-limit-value").textContent = money(split.gross_cents);
        rows.push(
          ["Total fees taking part early", money(quote.cost_cents)],
          ["Total fees taking all early", money(split.full_cost_cents)],
          ["Total fees waiting", money(decision.wait_cost_cents)]);
        facts(el("partial-dates"), [
          [dayLabel(quote.day) + " · After fee", money(split.gross_cents - quote.fee_cents)],
          [dayLabel(quote.regular_day) + " · In full", money(split.balance_cents)]
        ]);
        status.textContent = "The customer keeps " + money(quote.fee_cents) + " on the early portion. This plan saves at least "
          + money(Math.min(split.min_savings_cents.full, split.min_savings_cents.waiting))
          + " in fees across the tested fuel estimates compared with either whole-payment option.";
        const extra = !showWhole && split.setup_difference_cents > 0 ? " Set aside " + money(split.setup_difference_cents)
          + " more than the better whole-payment plan." : "";
        boundary.textContent = "This is the smallest advance that keeps both freight invoices in full across all "
          + quote.budget_count + " fuel estimates." + extra;
        if (!quote.road_cash_cents) boundary.textContent += " After setup, " + money(split.wallet_after_cents) + " stays in your wallet.";
        apply.textContent = "Use the partial-payment plan";
      } else if (decision) {
        heading.textContent = decision.take_offer ? "Take the early payment" : "Keep the regular payment date";
        rows.push(["Total cost with the early offer", money(decision.early_cost_cents)],
          ["Total cost if you wait", money(decision.wait_cost_cents)]);
        el("offer-limit-value").textContent = decision.highest_fee_cents === null ? "None" : money(decision.highest_fee_cents);
        boundary.textContent = decision.highest_fee_cents === null
          ? "Waiting stays competitive in at least one estimate even if early payment is free."
          : "With " + money(quote.capacity_cents) + " of reserve access, this is the highest fee that saves money across all " + quote.budget_count + " fuel estimates.";
        if (decision.take_offer) {
          status.textContent = "Arrange " + money(quote.cents - decision.fee_cents) + " on " + dayLabel(decision.early_day)
            + " with the customer. After the fee, you save at least " + money(decision.least_saved_cents) + " across the " + quote.budget_count + " estimates.";
        } else {
          const extra = decision.early_cost_cents - decision.wait_cost_cents;
          const reason = extra > 0 ? "Early pay costs " + money(extra) + " extra on this bill."
            : extra === 0 ? "Both choices cost the same on this bill." : "Early pay saves money on this bill, but loses its advantage in another estimate.";
          status.textContent = "Collect the full " + money(quote.cents) + " on " + dayLabel(decision.regular_day) + ". " + reason;
        }
        apply.textContent = decision.take_offer ? "Use the early-payment plan" : "Use the regular payment date";
        if (decision.partial_checked) {
          boundary.textContent += " A smaller advance that keeps both freight invoices in full did not beat both choices across every estimate.";
        }
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
      if (quote.road_cash_cents) {
        boundary.textContent += quote.wallet_return_cents
          ? " Review a " + money(quote.wallet_return_cents) + " return from your reserve to reach your road cash target before paying."
          : " Your wallet keeps " + money(quote.wallet_after_cents) + " after reserve setup.";
      }
      if (quote.requested_limit_cents > quote.available_reserve_cents)
        boundary.textContent += " Your wallet and vault can cover up to " + money(quote.available_reserve_cents) + " of the requested limit.";
      const comparison = el("surcharge-facts");
      facts(comparison, rows);
      const chosenRow = decision ? (decision.take_offer ? 0 : 1) : rows.length - 2;
      comparison.children[chosenRow].classList.add("chosen-cost");
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

  el("compare-whole").onclick = () => {
    if (!checked || apply.disabled) return;
    partial.checked = false;
    clear();
    updateOfferFields();
    form.requestSubmit();
  };

  el("compare-budget").onclick = event => {
    if (!checked || apply.disabled) return;
    budget.value = (Number(event.currentTarget.value) / 100).toFixed(2);
    el("reserve-limit-field").open = true;
    clear();
    updateOfferFields();
    form.requestSubmit();
  };

  apply.onclick = async () => {
    if (!checked || apply.disabled) return;
    const body = checked;
    const token = revision;
    apply.disabled = check.disabled = amount.disabled = day.disabled = road.disabled = budget.disabled = offer.disabled = partial.disabled = regular.disabled = discount.disabled = true;
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
      apply.disabled = check.disabled = amount.disabled = day.disabled = road.disabled = budget.disabled = offer.disabled = false;
      updateOfferFields();
    }
  };
  return open;
}
