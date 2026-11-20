import { el, money } from "./common.js";

function drawTicket(id, { x, y, depth, amount, source, load }) {
  const ticket = el(id);
  ticket.style.display = amount ? "" : "none";
  if (!amount) return;
  ticket.setAttribute("transform", `translate(${x} ${y})`);
  ticket.querySelector(".face").setAttribute("transform", "matrix(.92 -.22 .66 .36 0 0)");
  const right = 354 * .92, rise = -354 * .22;
  const offset = 145 * .66, drop = 145 * .36;
  ticket.querySelector(".slip-side").setAttribute("d",
    `M0 0l${offset} ${drop}v${depth}l${-offset} ${-drop}Z`);
  ticket.querySelector(".slip-front").setAttribute("d",
    `M${offset} ${drop}l${right} ${rise}v${depth}l${-right} ${-rise}Z`);
  const edges = [];
  for (let z = 1; z < depth; z += 2.2) {
    edges.push(`M0 ${z}l${offset} ${drop}l${right} ${rise}`);
  }
  ticket.querySelector(".slip-edges").setAttribute("d", edges.join(" "));
  ticket.querySelector(".ticket-value").textContent = money(amount).replace(/\.00$/, "");
  ticket.querySelector(".ticket-source").textContent = source;
  ticket.querySelector(".ticket-load").textContent = load;
}

export function drawFunding(plan, directSource) {
  const reserveDepth = 48 * plan.payment_cents / plan.bill_cents;
  drawTicket("direct-ticket", { x: 38, y: plan.payment_cents ? 175 : 127, depth: 48 - reserveDepth,
    amount: plan.other_funds_cents, source: directSource, load: plan.load });
  drawTicket("reserve-ticket", { x: 13, y: 93, depth: reserveDepth, amount: plan.payment_cents,
    source: "Fuel reserve", load: plan.load });
}
