import type { FeatureCollection } from "geojson";
import { buildBrief, facilities, renderBrief } from "./brief";
import { GROUPS, groupIndex, type Cell } from "./data";
import type { Drawer } from "./drawer";
import { day, el, hourLabel, hourRange, num, pct, perDay, svg } from "./format";
import { createMap, LINK_CAP, LINK_RAMP, rampCss, TIER_COLORS, TRAFFIC_RAMP, type MapHandle, type MapState, type OverlayName } from "./map";
import type { Metro } from "./metros";
import type { Band, ClassGroup, Findings, TrackFile, View } from "./types";

interface Choice<T extends string> {
  value: T;
  label: string;
}

function segmented<T extends string>(name: string, legend: string, choices: Choice<T>[], initial: T, onChange: (value: T) => void): HTMLFieldSetElement {
  const field = el("fieldset", { class: "seg" }, [el("legend", {}, [legend])]);
  const group = el("div", { class: "seg-row" });
  for (const choice of choices) {
    const id = `${name}-${choice.value}`;
    const input = el("input", { type: "radio", name, id, value: choice.value });
    input.checked = choice.value === initial;
    input.addEventListener("change", () => input.checked && onChange(choice.value));
    group.append(input, el("label", { for: id }, [choice.label]));
  }
  field.append(group);
  return field;
}

export interface Explorer {
  showOnMap(lat: number, lon: number): void;
}

/** Where the explorer puts its pieces. The shell owns the layout. */
export interface Slots {
  rail: HTMLElement;
  stage: HTMLElement;
  drawer: Drawer;
}

export function renderExplorer(
  slots: Slots,
  cells: Cell[],
  layers: FeatureCollection,
  findings: Findings,
  replayUrl: string,
  metro: Metro,
): Explorer {
  const days = findings.window.hours / 24;
  const windowSeconds = findings.window.hours * 3600;
  const places = facilities(layers);
  const state: MapState = { view: "traffic", band: "cruise", group: "all", from: 0, to: 23 };

  const { rail, stage, drawer } = slots;
  const mapNode = el("div", { class: "map", role: "application", "aria-label": `Map of the ${metro.name} ${metro.traced ? "proposed " : ""}operating area` });
  const legend = el("div", { class: "map-legend" });
  const hint = el("p", { class: "map-hint" }, ["Click the map to see who flies within 10 miles"]);
  const panel = el("div", { class: "brief" });
  const layerButton = el("button", { type: "button", class: "map-button", "aria-expanded": "false" }, ["Layers"]);
  const layerPanel = el("div", { class: "layer-panel" });
  layerPanel.hidden = true;
  layerButton.addEventListener("click", () => {
    layerPanel.hidden = !layerPanel.hidden;
    layerButton.setAttribute("aria-expanded", String(!layerPanel.hidden));
  });
  const closeLayers = () => {
    layerPanel.hidden = true;
    layerButton.setAttribute("aria-expanded", "false");
  };
  document.addEventListener("pointerdown", (event) => {
    if (!layerPanel.hidden && event.target instanceof Node && !layerPanel.parentElement?.contains(event.target)) closeLayers();
  });
  const replayBar = el("div", { class: "replay-bar" });
  stage.prepend(mapNode, legend, hint, el("div", { class: "layer-control" }, [layerButton, layerPanel]), replayBar);

  const map: MapHandle = createMap(mapNode, cells, layers, findings, pick);

  function pick(lat: number, lon: number): void {
    const brief = buildBrief(lat, lon, cells, places, findings);
    hint.hidden = true;
    renderBrief(panel, brief, findings, metro, { onAirport: (aLat, aLon) => map.flyTo(aLat, aLon, 11) });
    drawer.open({ body: panel, wide: false, label: `Who flies within ${brief.miles} miles of here`, onClose: () => map.clearPin() });
    // After open: opening runs the previous brief's close handler, which clears the old circle.
    map.setPin(lat, lon, brief.miles);
  }

  const summary = el("p", { class: "summary" });

  const viewField = segmented<View>(
    "view",
    "Show on the map",
    [
      { value: "traffic", label: "Where crewed aircraft fly low" },
      { value: "reception", label: "How low public tracking hears" },
      { value: "link", label: "Aircraft off the main radio band" },
    ],
    state.view,
    (value) => {
      state.view = value;
      update();
    },
  );
  viewField.classList.add("stacked");
  const bandField = segmented<Band>(
    "band",
    "Height above ground",
    [
      { value: "cruise", label: "Drone height" },
      { value: "low", label: "Below 1,200 ft" },
    ],
    state.band,
    (value) => {
      state.band = value;
      update();
    },
  );
  const classField = segmented<ClassGroup>(
    "class",
    "Crewed aircraft type",
    [
      { value: "all", label: "All" },
      { value: "rotorcraft", label: "Helicopters" },
      { value: "light", label: "Light" },
      { value: "larger", label: "Larger" },
    ],
    state.group,
    (value) => {
      state.group = value;
      update();
    },
  );

  // Hours: a drag-to-select chart, with two selects that do the same job from the keyboard.
  const hourField = el("fieldset", { class: "hours" }, [el("legend", {}, ["Hours, Central Time"])]);
  const hourChart = svg("svg", { viewBox: "0 0 240 64", class: "hour-chart", "aria-hidden": "true" });
  const option = (hour: number, label: string) => el("option", { value: String(hour) }, [label]);
  const fromSelect = el("select", { "aria-label": "From hour" }, Array.from({ length: 24 }, (_, hour) => option(hour, hourLabel(hour))));
  const toSelect = el("select", { "aria-label": "To hour" }, Array.from({ length: 24 }, (_, hour) => option(hour, hourLabel(hour + 1))));
  const reset = el("button", { type: "button", class: "link-button" }, ["All day"]);
  hourField.append(hourChart, el("div", { class: "hour-row" }, [fromSelect, el("span", {}, ["to"]), toSelect, reset]));
  fromSelect.addEventListener("change", () => {
    state.from = Number(fromSelect.value);
    if (state.to < state.from) state.to = state.from;
    update();
  });
  toSelect.addEventListener("change", () => {
    state.to = Number(toSelect.value);
    if (state.from > state.to) state.from = state.to;
    update();
  });
  reset.addEventListener("click", () => {
    state.from = 0;
    state.to = 23;
    update();
  });
  let anchor: number | null = null;
  const hourAt = (event: PointerEvent) => {
    const box = hourChart.getBoundingClientRect();
    return Math.min(23, Math.max(0, Math.floor(((event.clientX - box.left) / box.width) * 24)));
  };
  hourChart.addEventListener("pointerdown", (event) => {
    anchor = hourAt(event);
    state.from = anchor;
    state.to = anchor;
    hourChart.setPointerCapture(event.pointerId);
    update();
  });
  hourChart.addEventListener("pointermove", (event) => {
    if (anchor == null) return;
    const hour = hourAt(event);
    state.from = Math.min(anchor, hour);
    state.to = Math.max(anchor, hour);
    update();
  });
  hourChart.addEventListener("pointerup", () => (anchor = null));
  hourChart.addEventListener("dblclick", () => reset.click());

  // Name only the airspace this metro has: Dallas–Fort Worth has a Mode C veil and Class B, Austin has Class C.
  const hasLayer = (name: string) => layers.features.some((feature) => feature.properties?.layer === name);
  const hasVeil = hasLayer("veil");
  const classes = ["B", "C", "D"].filter((name) => hasLayer(`class_${name.toLowerCase()}`));
  const classList = (last: string) => (classes.length > 1 ? `${classes.slice(0, -1).join(", ")}${last}${classes[classes.length - 1]}` : classes.join(""));
  const airspaceLabel = [hasVeil ? "Mode C veil" : "", classes.length ? `Class ${classList(hasVeil ? ", " : " and ")}` : ""].filter(Boolean).join(" and ");
  const overlayField = el("fieldset", { class: "checks" }, [el("legend", {}, ["Draw on the map"])]);
  const overlays: [OverlayName, string, boolean][] = [
    ["standoff", "Runway stand-off in Zipline's exemption", true],
    ["facilities", "Airports and heliports", false],
    ["airspace", airspaceLabel || "Controlled airspace", false],
    ["busy", "Busiest spots away from runways", false],
  ];
  const overlayInputs = new Map<OverlayName, HTMLInputElement>();
  for (const [name, label, on] of overlays) {
    const input = el("input", { type: "checkbox", id: `overlay-${name}` });
    input.checked = on;
    map.setOverlay(name, on);
    input.addEventListener("change", () => {
      map.setOverlay(name, input.checked);
      drawLegend();
    });
    overlayInputs.set(name, input);
    overlayField.append(el("label", { for: input.id }, [input, label]));
  }

  // Replay
  const replayDay = day(findings.study.replay_date, true);
  const playLabel = `Replay ${replayDay}`;
  const play = el("button", { type: "button", class: "button" }, [playLabel]);
  play.title = "Tracks below 1,500 ft for one day, coloured by aircraft type. No aircraft are identified.";
  const scrub = el("input", { type: "range", min: "0", max: "86400", step: "60", value: "21600", "aria-label": "Time of day" });
  scrub.hidden = true;
  const clock = el("span", { class: "clock num" }, ["6:00 am"]);
  clock.hidden = true;
  const stop = el("button", { type: "button", class: "icon-button" }, ["Hide"]);
  stop.hidden = true;
  replayBar.append(play, scrub, clock, stop);
  let file: TrackFile | null = null;
  let frame = 0;
  let last = 0;
  const clockText = (secondsOfDay: number) => {
    const h = Math.floor(secondsOfDay / 3600) % 24;
    const m = Math.floor((secondsOfDay % 3600) / 60);
    return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
  };
  const showTime = (time: number) => {
    scrub.value = String(time);
    clock.textContent = clockText(time);
    map.setReplay(file, time);
  };
  const pause = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    play.textContent = "Play";
  };
  const tick = (now: number) => {
    const elapsed = Math.min(100, now - last);
    last = now;
    // Twenty minutes of the day for each second on screen.
    const next = Number(scrub.value) + elapsed * 1.2;
    if (next >= 86400) {
      showTime(86400);
      pause();
      return;
    }
    showTime(next);
    frame = requestAnimationFrame(tick);
  };
  play.addEventListener("click", async () => {
    if (!file) {
      play.disabled = true;
      play.textContent = "Loading tracks";
      try {
        file = (await fetch(replayUrl).then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        })) as TrackFile;
      } catch {
        play.textContent = "Tracks did not load. Try again";
        play.disabled = false;
        return;
      }
      play.disabled = false;
      scrub.hidden = false;
      clock.hidden = false;
      stop.hidden = false;
      drawLegend();
    }
    if (frame) {
      pause();
      return;
    }
    if (Number(scrub.value) >= 86400) scrub.value = "0";
    play.textContent = "Pause";
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      // No animation: show the whole hour at once and let the slider do the moving.
      showTime(Number(scrub.value));
      play.textContent = "Play";
      return;
    }
    last = performance.now();
    frame = requestAnimationFrame(tick);
  });
  scrub.addEventListener("input", () => showTime(Number(scrub.value)));
  stop.addEventListener("click", () => {
    pause();
    play.textContent = playLabel;
    file = null;
    scrub.hidden = true;
    clock.hidden = true;
    stop.hidden = true;
    map.setReplay(null, 0);
    drawLegend();
  });

  layerPanel.append(overlayField);
  rail.append(summary, viewField, bandField, classField, hourField);

  function drawHours(): void {
    hourChart.replaceChildren();
    const wanted = groupIndex(state.group);
    const totals = new Array<number>(24).fill(0);
    for (const cell of cells) {
      const grid = state.band === "cruise" ? cell.cruise : cell.low;
      for (let group = 0; group < 4; group += 1) {
        if (wanted >= 0 && group !== wanted) continue;
        for (let hour = 0; hour < 24; hour += 1) totals[hour] += grid[group * 24 + hour];
      }
    }
    const peak = Math.max(...totals, 1);
    totals.forEach((value, hour) => {
      const h = Math.max(value > 0 ? 1.5 : 0, (value / peak) * 46);
      hourChart.append(svg("rect", { x: hour * 10 + 1, y: 48 - h, width: 8, height: h, rx: 1.5, class: hour >= state.from && hour <= state.to ? "on" : "" }));
    });
    hourChart.append(svg("line", { x1: 0, x2: 240, y1: 48.5, y2: 48.5, class: "axis" }));
    for (const hour of [0, 6, 12, 18]) {
      const text = svg("text", { x: hour * 10 + 1, y: 60 });
      text.textContent = ["12 am", "6 am", "noon", "6 pm"][hour / 6];
      hourChart.append(text);
    }
  }

  function swatch(color: string, label: string, extra = ""): HTMLElement {
    return el("li", {}, [el("i", { class: extra, style: `background-color:${color}` }), label]);
  }

  function drawLegend(): void {
    legend.replaceChildren();
    const rgb = (c: number[]) => `rgb(${c.join(",")})`;
    if (file) {
      legend.append(el("p", { class: "legend-title" }, ["Tracks below 1,500 ft"]), el("ul", { class: "legend-list" }, GROUPS.map((group) => swatch(group.color, group.label))));
    } else if (state.view === "traffic") {
      const cap = map.trafficCap(state.band);
      legend.append(
        el("p", { class: "legend-title" }, [`Crewed aircraft time ${state.band === "cruise" ? "at drone height (80–580 ft)" : "below 1,200 ft"}, per day`]),
        el("div", { class: "legend-ramp", style: `background:${rampCss(TRAFFIC_RAMP)}` }),
        el("div", { class: "legend-ticks" }, [el("span", {}, ["0"]), el("span", {}, [`${num(cap / 4, cap < 8 ? 1 : 0)} min`]), el("span", {}, [`${num(cap)} min or more`])]),
        el("ul", { class: "legend-list" }, [swatch("rgb(222,227,224)", "Fewer than 3 aircraft")]),
      );
    } else if (state.view === "reception") {
      legend.append(
        el("p", { class: "legend-title" }, ["Lowest that public tracking heard aircraft"]),
        el("ul", { class: "legend-list" }, [
          swatch(rgb(TIER_COLORS[0]), "Drone height, below 580 ft"),
          swatch(rgb(TIER_COLORS[1]), "Below 1,200 ft"),
          swatch(rgb(TIER_COLORS[2]), "Only higher up: unproven"),
          swatch("#f4f6f4", "Nothing heard", "hatch"),
        ]),
      );
    } else {
      legend.append(
        el("p", { class: "legend-title" }, ["Share of low time not on 1090 MHz"]),
        el("div", { class: "legend-ramp", style: `background:${rampCss(LINK_RAMP)}` }),
        el("div", { class: "legend-ticks" }, [el("span", {}, ["0%"]), el("span", {}, [pct(LINK_CAP / 2)]), el("span", {}, [`${pct(LINK_CAP)} or more`])]),
        el("ul", { class: "legend-list" }, [swatch("rgb(222,227,224)", "Fewer than 3 aircraft")]),
      );
    }
    const areaLabel = metro.traced ? "Area in the FAA's draft assessment, traced" : "Area in Zipline's FAA assessment";
    const lines = el("ul", { class: "legend-list lines" }, [el("li", {}, [el("i", { class: "line area" }), areaLabel])]);
    if (overlayInputs.get("standoff")?.checked) lines.append(el("li", {}, [el("i", { class: "line standoff" }), "3 NM from a public-use runway"]));
    if (overlayInputs.get("airspace")?.checked) {
      if (hasVeil) lines.append(el("li", {}, [el("i", { class: "line veil" }), "Mode C veil"]));
      if (classes.length) lines.append(el("li", {}, [el("i", { class: "line classb" }), `Class ${classList(" and ")} surface areas`]));
    }
    if (overlayInputs.get("busy")?.checked) lines.append(el("li", {}, [el("i", { class: "line busy" }), "Busiest areas off-airport"]));
    if (overlayInputs.get("facilities")?.checked) lines.append(el("li", {}, [el("i", { class: "dot public" }), "Public airport"]), el("li", {}, [el("i", { class: "dot private" }), "Private airport or heliport"]), el("li", {}, [el("i", { class: "dot medical" }), "Medical heliport"]));
    legend.append(lines);
  }

  function update(): void {
    const traffic = state.view === "traffic";
    bandField.disabled = !traffic;
    classField.disabled = !traffic;
    hourField.disabled = !traffic;
    fromSelect.value = String(state.from);
    toSelect.value = String(state.to);
    reset.hidden = state.from === 0 && state.to === 23;
    summary.replaceChildren();
    if (state.view === "traffic") {
      const wanted = groupIndex(state.group);
      let total = 0;
      for (const cell of cells) {
        const grid = state.band === "cruise" ? cell.cruise : cell.low;
        for (let group = 0; group < 4; group += 1) {
          if (wanted >= 0 && group !== wanted) continue;
          for (let hour = state.from; hour <= state.to; hour += 1) total += grid[group * 24 + hour];
        }
      }
      const hours = state.to - state.from + 1;
      const present = total / (windowSeconds * (hours / 24));
      const who = state.group === "all" ? "crewed aircraft" : state.group === "rotorcraft" ? "helicopters" : state.group === "light" ? "light aircraft" : "larger aircraft";
      const band = state.band === "cruise" ? "near Zipline's 330 ft cruise height (80–580 ft)" : "below 1,200 ft";
      const when = hours === 24 ? "at a typical moment" : `between ${hourRange(state.from, state.to + 1)}`;
      summary.append(el("span", { class: "num" }, [num(present, present < 10 ? 1 : 0)]), ` ${who} ${band} ${when}, across ${metro.traced ? `the proposed ${metro.name} area` : `Zipline's ${metro.name} delivery area`}.`);
    } else if (state.view === "reception") {
      summary.append(el("span", { class: "num" }, [pct(findings.q2.heard_cruise_share)]), " of the area is where public tracking was shown to hear aircraft at drone height. Pale areas are unproven, not empty.");
    } else {
      summary.append(el("span", { class: "num" }, [`at least ${pct(findings.q5.single_band_miss_share)}`]), " of low traffic was on the second band (978 MHz) or not broadcasting, so a receiver on 1090 MHz alone would not hear it.");
    }
    map.setState({ ...state });
    drawHours();
    drawLegend();
  }

  update();

  return {
    showOnMap(lat, lon) {
      const busy = overlayInputs.get("busy");
      if (busy && !busy.checked) {
        busy.checked = true;
        map.setOverlay("busy", true);
      }
      map.flyTo(lat, lon, 10.5);
      pick(lat, lon);
      drawLegend();
    },
  };
}
