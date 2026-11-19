import { el } from "./common.js";

const layout = window.matchMedia("(max-width: 760px)");
const tabs = [el("fees-tab"), el("dates-tab")];
let active = tabs[0];

function draw() {
  document.querySelector(".bill-tabs").hidden = !layout.matches;
  for (const tab of tabs) {
    const panel = el(tab.getAttribute("aria-controls"));
    tab.setAttribute("aria-selected", String(tab === active));
    tab.tabIndex = tab === active ? 0 : -1;
    panel.hidden = layout.matches && tab !== active;
    if (layout.matches) {
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
    } else {
      panel.removeAttribute("role");
      if (panel.id === "plan-panel") panel.setAttribute("aria-labelledby", "plan-head");
      else panel.removeAttribute("aria-labelledby");
    }
  }
}

export function openBillTab(name) {
  active = el(name);
  draw();
}

for (const tab of tabs) {
  tab.onclick = () => openBillTab(tab.id);
  tab.onkeydown = event => {
    const next = { ArrowLeft: tabs[1 - tabs.indexOf(tab)], ArrowRight: tabs[1 - tabs.indexOf(tab)], Home: tabs[0], End: tabs[1] }[event.key];
    if (next) { event.preventDefault(); openBillTab(next.id); next.focus(); }
  };
}
layout.addEventListener("change", () => {
  if (layout.matches && el("dates-panel").contains(document.activeElement)) active = tabs[1];
  if (layout.matches && el("plan-panel").contains(document.activeElement)) active = tabs[0];
  draw();
});
draw();
