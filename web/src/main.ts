import "@fontsource-variable/figtree";
import "./style.css";
import { inject } from "@vercel/analytics";
import type { FeatureCollection } from "geojson";
import { decodeCells } from "./data";
import { createDrawer } from "./drawer";
import { renderExplorer } from "./explorer";
import { el, num } from "./format";
import { answers, buildSections } from "./report";
import { installTips } from "./tip";
import type { Findings, RawCell } from "./types";

const base = import.meta.env.BASE_URL;

async function load<T>(path: string): Promise<T> {
  const response = await fetch(`${base}${path}`);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return (await response.json()) as T;
}

async function start(app: HTMLElement): Promise<void> {
  const [findings, rawCells, layers] = await Promise.all([
    load<Findings>("data/findings.json"),
    load<RawCell[]>("data/cells.json"),
    load<FeatureCollection>("data/layers.geojson"),
  ]);
  const cells = decodeCells(rawCells);
  app.replaceChildren();

  const rail = el("div", { class: "rail", id: "filters" });
  const stage = el("div", { class: "stage" });
  const drawer = createDrawer(stage);
  const bar = el("header", { class: "topbar" });
  const strip = el("div", { class: "strip", role: "group", "aria-label": "Findings" });
  // In the page before the map is made, so the map can measure its container.
  app.append(bar, strip, el("div", { class: "workspace" }, [rail, stage]));
  const explorer = renderExplorer({ rail, stage, drawer }, cells, layers, findings, `${base}data/replay.json`);
  const sections = buildSections(findings, { showOnMap: explorer.showOnMap });

  function openSection(id: string): void {
    const section = sections.find((item) => item.id === id);
    if (!section) return;
    const tabs = el("div", { class: "tabs", role: "tablist" });
    for (const item of sections) {
      const tab = el("button", { type: "button", role: "tab", "aria-selected": String(item.id === id) }, [item.tab]);
      tab.addEventListener("click", () => openSection(item.id));
      tabs.append(tab);
    }
    drawer.open({ body: section.node, wide: true, label: section.tab, tabs });
  }

  const nav = el("nav", { class: "bar-nav", "aria-label": "More" });
  for (const [id, label] of [["method", "Method"], ["limits", "Limits"], ["sources", "Sources"], ["about", "About"]]) {
    const button = el("button", { type: "button" }, [label]);
    button.addEventListener("click", () => openSection(id));
    nav.append(button);
  }
  nav.append(el("a", { class: "credit", href: "https://aab.is", target: "_blank", rel: "noopener" }, ["Built by Aabis"]));
  const filterToggle = el("button", { type: "button", class: "filter-toggle", "aria-controls": "filters", "aria-expanded": "false" }, ["Filters"]);
  filterToggle.addEventListener("click", () => {
    const open = rail.classList.toggle("open");
    filterToggle.setAttribute("aria-expanded", String(open));
  });
  bar.append(
    el("h1", {}, ["Who shares the low sky with Zipline in Dallas–Fort Worth"]),
    el("p", { class: "bar-note" }, ["One week of public flight tracking over the area in Zipline's FAA assessment. Based on public data only."]),
    filterToggle,
    nav,
  );
  for (const answer of answers(findings)) {
    const chip = el("button", { type: "button", class: "chip" }, [
      el("span", { class: "num" }, [answer.value]),
      el("span", { class: "chip-label" }, [answer.label]),
    ]);
    chip.addEventListener("click", () => openSection(answer.id));
    strip.append(chip);
  }

  app.append(welcome(findings));
  installTips(document.body);
}

/** A short card on arrival that says what this is and how to use it. */
function welcome(findings: Findings): HTMLElement {
  const go = el("button", { type: "button", class: "button" }, ["Explore the map"]);
  const card = el("div", { class: "welcome-card", role: "dialog", "aria-modal": "true", "aria-label": "About this map" }, [
    el("h2", {}, ["Who shares the low sky with Zipline in Dallas–Fort Worth?"]),
    el("p", {}, [
      "Zipline's delivery drones cruise at 330 ft inside the area the FAA has approved around Dallas–Fort Worth. Helicopters and small planes fly low there too.",
    ]),
    el("p", {}, [
      `This map uses ${num(findings.window.hours / 24)} days of public flight tracking to show where those crewed aircraft fly at drone height, whether they broadcast their position, and where public tracking cannot see them.`,
    ]),
    el("ul", {}, [
      el("li", {}, [el("strong", {}, ["Click the map"]), " to see who flies within 10 miles of any point, the reach of one Zipline site."]),
      el("li", {}, [el("strong", {}, ["Use the filters"]), " on the left for helicopters only, a time of day, or a different view."]),
      el("li", {}, [el("strong", {}, ["Open a number"]), " along the top for the finding behind it."]),
    ]),
    go,
  ]);
  const overlay = el("div", { class: "welcome" }, [card]);
  const close = () => overlay.remove();
  go.addEventListener("click", close);
  overlay.addEventListener("click", (event) => event.target === overlay && close());
  document.addEventListener("keydown", (event) => event.key === "Escape" && close());
  queueMicrotask(() => go.focus());
  return overlay;
}

// Page views only, no cookies. Does nothing until Web Analytics is enabled for the project on Vercel.
inject();

const app = document.querySelector<HTMLElement>("#app");
if (app) {
  start(app).catch((error: unknown) => {
    app.replaceChildren(
      el("p", { class: "load-error" }, [
        `The survey data did not load (${error instanceof Error ? error.message : "unknown error"}). Run the pipeline to write web/public/data, then reload.`,
      ]),
    );
  });
}
