import type { Feature, FeatureCollection, Point } from "geojson";
import { bearing, GROUPS, milesBetween, type Cell } from "./data";
import { coord, el, feet, hourRange, num, pct, perDay, svg } from "./format";
import type { Metro } from "./metros";
import type { Findings } from "./types";

const NM_PER_MI = 0.868976;

interface Facility {
  kind: "airport" | "heliport";
  id: string;
  name: string;
  isPublic: boolean;
  medical: boolean;
  lat: number;
  lon: number;
  visits?: number;
  lowestFt?: number;
  toGround?: number;
}

export function facilities(layers: FeatureCollection): Facility[] {
  const out: Facility[] = [];
  for (const feature of layers.features as Feature<Point>[]) {
    const props = feature.properties ?? {};
    if (props.layer !== "airport" && props.layer !== "heliport") continue;
    out.push({
      kind: props.layer,
      id: props.id,
      name: props.name,
      isPublic: Boolean(props.public),
      medical: Boolean(props.medical),
      lon: feature.geometry.coordinates[0],
      lat: feature.geometry.coordinates[1],
      visits: props.visits,
      lowestFt: props.lowest_ft,
      toGround: props.to_ground,
    });
  }
  return out;
}

export interface Brief {
  lat: number;
  lon: number;
  miles: number;
  cells: number;
  /** Share of the circle that falls inside the operating area. */
  insideShare: number;
  cruisePresent: number;
  lowPresent: number;
  cruiseMinutesPerDay: number;
  lowMinutesPerDay: number;
  cruiseShareOfArea: number;
  /** Cruise-adjacent seconds by class group. */
  byGroup: number[];
  /** Cruise-adjacent seconds by hour. */
  byHour: number[];
  busiestStart: number;
  busiestShare: number;
  standOffAreaShare: number;
  standOffTrafficShare: number | null;
  ruleAreaShare: number;
  tierShares: [number, number, number, number];
  offBandShare: number | null;
  lowAircraftCells: number;
  publicAirports: (Facility & { nm: number; dir: string })[];
  privateStrips: number;
  heliports: number;
  medicalHeliports: number;
  nearestPublic: (Facility & { nm: number; dir: string }) | null;
  cell: Cell | null;
}

export function buildBrief(
  lat: number,
  lon: number,
  cells: Cell[],
  places: Facility[],
  findings: Findings,
): Brief {
  const miles = findings.study.site_radius_mi;
  const windowSeconds = findings.window.hours * 3600;
  const days = findings.window.hours / 24;
  const inside = cells.filter((cell) => milesBetween(lat, lon, cell.lat, cell.lon) <= miles);
  const byGroup = [0, 0, 0, 0];
  const byHour = new Array<number>(24).fill(0);
  const tiers: [number, number, number, number] = [0, 0, 0, 0];
  let cruise = 0;
  let low = 0;
  let standOffCells = 0;
  let standOffSeconds = 0;
  let ruleCells = 0;
  let onBand = 0;
  let linkTotal = 0;
  let nearest: Cell | null = null;
  let nearestMiles = Infinity;
  for (const cell of inside) {
    cruise += cell.cruiseTotal;
    low += cell.lowTotal;
    for (let group = 0; group < 4; group += 1) {
      for (let hour = 0; hour < 24; hour += 1) {
        const value = cell.cruise[group * 24 + hour];
        byGroup[group] += value;
        byHour[hour] += value;
      }
    }
    tiers[cell.tier] += 1;
    if (cell.inStandOff) standOffCells += 1;
    standOffSeconds += cell.standOffSeconds;
    if (cell.inRule) ruleCells += 1;
    onBand += cell.links[0];
    linkTotal += cell.links[0] + cell.links[1] + cell.links[2] + cell.links[3];
    const d = milesBetween(lat, lon, cell.lat, cell.lon);
    if (d < nearestMiles) {
      nearestMiles = d;
      nearest = cell;
    }
  }
  let busiestStart = 0;
  let busiestSum = -1;
  for (let hour = 0; hour < 24; hour += 1) {
    const sum = byHour[hour] + byHour[(hour + 1) % 24] + byHour[(hour + 2) % 24];
    if (sum > busiestSum) {
      busiestSum = sum;
      busiestStart = hour;
    }
  }
  const count = inside.length || 1;
  const located = places
    .map((place) => ({
      ...place,
      nm: milesBetween(lat, lon, place.lat, place.lon) * NM_PER_MI,
      dir: bearing(place.lat, place.lon, lat, lon),
    }))
    .sort((a, b) => a.nm - b.nm);
  const within = located.filter((place) => place.nm <= miles * NM_PER_MI);
  const areaCruise = findings.q3.cruise.aircraft_hours * 3600;
  return {
    lat,
    lon,
    miles,
    cells: inside.length,
    // Cell area is taken from the survey itself: H3 cells here are larger than the global mean.
    insideShare: Math.min(1, (inside.length * (findings.q1.area.operating_sq_mi / cells.length)) / (Math.PI * miles * miles)),
    cruisePresent: cruise / windowSeconds,
    lowPresent: low / windowSeconds,
    cruiseMinutesPerDay: cruise / 60 / days,
    lowMinutesPerDay: low / 60 / days,
    cruiseShareOfArea: areaCruise > 0 ? cruise / areaCruise : 0,
    byGroup,
    byHour,
    busiestStart,
    busiestShare: cruise > 0 ? busiestSum / cruise : 0,
    standOffAreaShare: standOffCells / count,
    standOffTrafficShare: low > 0 ? standOffSeconds / low : null,
    ruleAreaShare: ruleCells / count,
    tierShares: [tiers[0] / count, tiers[1] / count, tiers[2] / count, tiers[3] / count],
    offBandShare: linkTotal > 0 ? (linkTotal - onBand) / linkTotal : null,
    lowAircraftCells: inside.filter((cell) => cell.lowAircraft >= 3).length,
    publicAirports: within.filter((place) => place.kind === "airport" && place.isPublic),
    privateStrips: within.filter((place) => place.kind === "airport" && !place.isPublic).length,
    heliports: within.filter((place) => place.kind === "heliport").length,
    medicalHeliports: within.filter((place) => place.kind === "heliport" && place.medical).length,
    nearestPublic: located.find((place) => place.kind === "airport" && place.isPublic) ?? null,
    cell: nearestMiles <= 1.5 ? nearest : null,
  };
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

function where(brief: Brief): string {
  const near = brief.nearestPublic;
  if (!near) return coord(brief.lat, brief.lon);
  if (near.nm < 0.5) return `at ${near.name}`;
  return `${num(near.nm, near.nm < 10 ? 1 : 0)} NM ${near.dir} of ${near.name}`;
}

function receptionSentence(brief: Brief): string {
  const [drone, low, higher, none] = brief.tierShares;
  if (drone + low < 0.05) {
    return `Public tracking has not shown it can hear below 1,200 ft anywhere in this circle, so the traffic figures here are a floor and may be far too low.`;
  }
  return `Public tracking heard aircraft at drone height over ${pct(drone)} of the circle and below 1,200 ft over ${pct(drone + low)}. The remaining ${pct(higher + none)} is unproven, so low traffic there may be undercounted.`;
}

function areaName(metro: Metro): string {
  return metro.traced ? "traced operating area" : "published operating area";
}

/** The brief as plain text, for pasting into a note or a ticket. */
export function briefText(brief: Brief, findings: Findings, metro: Metro): string {
  const [first, last] = findings.study.window;
  const groups = GROUPS.map((group, i) => ({ label: group.label, share: brief.byGroup[i] }))
    .filter((group) => group.share > 0)
    .sort((a, b) => b.share - a.share);
  const total = brief.byGroup.reduce((sum, value) => sum + value, 0) || 1;
  const lines = [
    `Low-altitude traffic brief: within ${brief.miles} miles of ${coord(brief.lat, brief.lon)} (${where(brief)})`,
    `Source: ${findings.study.name}, adsb.lol public feed, ${first} to ${last} UTC. Based on public data only.`,
    "",
    `80-580 ft above ground: ${num(brief.cruisePresent, 2)} crewed aircraft on average (${perDay(brief.cruiseMinutesPerDay)}), ${pct(brief.cruiseShareOfArea, 1)} of the whole operating area's total.`,
    `Surface-1,200 ft: ${num(brief.lowPresent, 2)} on average (${perDay(brief.lowMinutesPerDay)}).`,
    groups.length ? `Mix at 80-580 ft: ${groups.map((group) => `${group.label.toLowerCase()} ${pct(group.share / total)}`).join(", ")}.` : "",
    brief.cruiseMinutesPerDay > 0
      ? `Busiest three hours: ${hourRange(brief.busiestStart, brief.busiestStart + 3)} Central (${pct(brief.busiestShare)} of the time).`
      : "",
    `Runway stand-off: ${pct(brief.standOffAreaShare)} of the circle is within 3 NM of a public-use runway` +
      (brief.standOffTrafficShare == null ? "." : `; ${pct(brief.standOffTrafficShare)} of the low traffic is there.`),
    `Broadcast rule: ADS-B Out is required at low altitude over ${pct(brief.ruleAreaShare)} of the circle.`,
    brief.offBandShare == null ? "" : `Not on 1090 MHz: at least ${pct(brief.offBandShare, 1)} of low time (UAT, or no broadcast at all).`,
    `Public-use airports inside: ${brief.publicAirports.length ? brief.publicAirports.map((a) => `${a.name} (${a.id}, ${num(a.nm, 1)} NM)`).join("; ") : "none"}.`,
    `Also inside: ${brief.privateStrips} private-use airports, ${brief.heliports} heliports (${brief.medicalHeliports} medical).`,
    `Reception: ${receptionSentence(brief)}`,
    brief.insideShare < 0.93 ? `Only ${pct(brief.insideShare)} of this circle is inside the ${areaName(metro)}; the rest is not surveyed.` : "",
    "",
    "Counts are what a ground receiver network heard: a lower bound, not an encounter or risk estimate.",
  ];
  return lines.filter((line, i) => line !== "" || lines[i - 1] !== "").join("\n").trim();
}

function row(label: string, value: string, note?: string): HTMLElement {
  return el("div", { class: "brief-row" }, [
    el("dt", {}, [label]),
    el("dd", {}, [el("span", { class: "num" }, [value]), note ? el("span", { class: "note" }, [note]) : null]),
  ]);
}

function hoursChart(byHour: number[], start: number): SVGElement {
  const peak = Math.max(...byHour, 1);
  const chart = svg("svg", { viewBox: "0 0 240 62", class: "spark", role: "img", "aria-label": "Aircraft time at 80 to 580 feet by hour of day, Central Time" });
  byHour.forEach((value, hour) => {
    const h = Math.max(value > 0 ? 1.5 : 0, (value / peak) * 44);
    const busy = [0, 1, 2].some((k) => (start + k) % 24 === hour);
    chart.append(
      svg("rect", {
        x: hour * 10 + 1,
        y: 46 - h,
        width: 8,
        height: h,
        rx: 1.5,
        class: busy ? "on" : "",
        "data-tip": `${pct(value / (byHour.reduce((a, b) => a + b, 0) || 1))} of the time\n${hourRange(hour, hour + 1)}`,
      }),
    );
  });
  chart.append(svg("line", { x1: 0, x2: 240, y1: 46.5, y2: 46.5, class: "axis" }));
  for (const hour of [0, 6, 12, 18]) {
    const label = svg("text", { x: hour * 10 + 1, y: 58 });
    label.textContent = ["12 am", "6 am", "noon", "6 pm"][hour / 6];
    chart.append(label);
  }
  return chart;
}

function profileChart(cell: Cell): SVGElement {
  const peak = Math.max(...cell.profile, 1);
  const chart = svg("svg", { viewBox: "0 0 240 132", class: "profile", role: "img", "aria-label": "Aircraft time by height above ground in the clicked cell" });
  cell.profile.forEach((value, bin) => {
    const w = (value / peak) * 150;
    const y = 120 - (bin + 1) * 10;
    chart.append(
      svg("rect", {
        x: 44,
        y: y + 1,
        width: Math.max(value > 0 ? 1.5 : 0, w),
        height: 8,
        rx: 1.5,
        "data-tip": `${num(value / 60)} aircraft-minutes in the week\n${bin * 100}–${(bin + 1) * 100} ft above ground`,
      }),
    );
  });
  for (const [alt, label] of [[330, "330 ft cruise"], [400, "400 ft cap"]] as [number, string][]) {
    const y = 120 - alt / 10;
    chart.append(svg("line", { x1: 44, x2: 236, y1: y, y2: y, class: "ref" }));
    const text = svg("text", { x: 236, y: y - 2.5, "text-anchor": "end" });
    text.textContent = label;
    chart.append(text);
  }
  for (const alt of [0, 600, 1200]) {
    const text = svg("text", { x: 40, y: 120 - alt / 10 + 3, "text-anchor": "end" });
    text.textContent = alt === 0 ? "ground" : `${num(alt)} ft`;
    chart.append(text);
  }
  chart.append(svg("line", { x1: 44, x2: 44, y1: 0, y2: 120, class: "axis" }));
  return chart;
}

export function renderBrief(
  panel: HTMLElement,
  brief: Brief,
  findings: Findings,
  metro: Metro,
  actions: { onAirport: (lat: number, lon: number) => void },
): void {
  panel.replaceChildren();
  const radius = metro.traced
    ? "Ten miles is how far one Zipline site delivers in the Dallas–Fort Worth assessment; the Texas draft limits each round trip to 20 miles."
    : "Ten miles is how far one Zipline site delivers in the FAA assessment.";
  panel.append(
    el("p", { class: "muted" }, [`${coord(brief.lat, brief.lon)}, ${where(brief)}. ${radius} This is any point you pick, not a Zipline site.`]),
  );
  if (brief.cells === 0) {
    panel.append(el("p", {}, [`This point is outside the ${areaName(metro)}. Click inside the outline.`]));
    return;
  }
  if (brief.insideShare < 0.93) {
    panel.append(el("p", { class: "caution" }, [`Only ${pct(brief.insideShare)} of this circle is inside the ${areaName(metro)}. The rest is not surveyed.`]));
  }
  const total = brief.byGroup.reduce((sum, value) => sum + value, 0);
  const list = el("dl", { class: "brief-list" }, [
    row("At drone height (80–580 ft)", `${num(brief.cruisePresent, 2)} aircraft`, `on average, ${perDay(brief.cruiseMinutesPerDay)}. ${pct(brief.cruiseShareOfArea, 1)} of the whole area's total.`),
    row("Below 1,200 ft", `${num(brief.lowPresent, 2)} aircraft`, `on average, ${perDay(brief.lowMinutesPerDay)}.`),
  ]);
  panel.append(list);
  if (total > 0) {
    const mix = el("div", { class: "mix", role: "img", "aria-label": "Aircraft mix at 80 to 580 feet" });
    const legend = el("ul", { class: "mix-legend" });
    GROUPS.forEach((group, i) => {
      const share = brief.byGroup[i] / total;
      if (share <= 0) return;
      const segment = el("span", { style: `flex:${share};background:${group.color}`, "data-tip": `${pct(share)}\n${group.label}, 80–580 ft` });
      mix.append(segment);
      legend.append(el("li", {}, [el("i", { style: `background:${group.color}` }), `${group.label} ${pct(share)}`]));
    });
    panel.append(el("div", { class: "brief-block" }, [el("h4", {}, ["Who is at drone height"]), mix, legend]));
    panel.append(
      el("div", { class: "brief-block" }, [
        el("h4", {}, ["When"]),
        hoursChart(brief.byHour, brief.busiestStart),
        el("p", { class: "muted" }, [`Busiest three hours: ${hourRange(brief.busiestStart, brief.busiestStart + 3)}, with ${pct(brief.busiestShare)} of the time.`]),
      ]),
    );
  }
  const airports = el("ul", { class: "airport-list" });
  for (const airport of brief.publicAirports) {
    const button = el("button", { type: "button", class: "link-button" }, [`${airport.name} (${airport.id})`]);
    button.addEventListener("click", () => actions.onAirport(airport.lat, airport.lon));
    const heard = !airport.visits ? "" : (airport.lowestFt ?? 0) <= 50 ? ", arrivals heard to the ground" : `, arrivals heard down to ${feet(airport.lowestFt)}`;
    airports.append(el("li", {}, [button, ` ${num(airport.nm, 1)} NM${heard}`]));
  }
  panel.append(
    el("div", { class: "brief-block" }, [
      el("h4", {}, ["Runways and helipads nearby"]),
      el("p", {}, [
        `${pct(brief.standOffAreaShare)} of the circle is within 3 NM of a public-use runway` +
          (brief.standOffTrafficShare == null ? "." : `, and ${pct(brief.standOffTrafficShare)} of its low traffic is there.`),
      ]),
      brief.publicAirports.length ? airports : el("p", { class: "muted" }, ["No public-use airport inside the circle."]),
      el("p", { class: "muted" }, [
        `Also inside: ${plural(brief.privateStrips, "private-use airport")} and ${plural(brief.heliports, "heliport")}, ${brief.medicalHeliports} of them medical.`,
      ]),
    ]),
  );
  panel.append(
    el("div", { class: "brief-block" }, [
      el("h4", {}, ["Do they broadcast their position"]),
      el("p", {}, [
        `ADS-B Out is required at low altitude over ${pct(brief.ruleAreaShare)} of the circle.` +
          (brief.offBandShare == null
            ? ""
            : ` At least ${pct(brief.offBandShare, 1)} of the low time heard here was not on 1090 MHz.`),
      ]),
    ]),
  );
  panel.append(el("div", { class: "brief-block" }, [el("h4", {}, ["How far to trust this"]), el("p", {}, [receptionSentence(brief)])]));
  if (brief.cell && brief.cell.lowTotal > 0) {
    panel.append(
      el("div", { class: "brief-block" }, [
        el("h4", {}, ["Heights in the clicked cell"]),
        profileChart(brief.cell),
        el("p", { class: "muted" }, [`${brief.cell.lowAircraft} aircraft below 1,200 ft in the week.`]),
      ]),
    );
  }
  const copy = el("button", { type: "button", class: "button" }, ["Copy brief as text"]);
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(briefText(brief, findings, metro));
      copy.textContent = "Copied";
    } catch {
      copy.textContent = "Copy failed";
    }
    window.setTimeout(() => (copy.textContent = "Copy brief as text"), 1800);
  });
  panel.append(el("div", { class: "brief-actions" }, [copy]));
}
