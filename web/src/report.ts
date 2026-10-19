import { bars, dotPlot, histogram, hourColumns, stack, table } from "./charts";
import { GROUPS } from "./data";
import { day, el, feet, hourRange, num, pct, perDay } from "./format";
import type { Metro } from "./metros";
import type { Findings, Hotspot } from "./types";

const TIER_HEX = ["#0f6b5c", "#6fb3a4", "#dde4e0", "#eef0ee"];
const LINK_COLORS = { adsb_1090: "#2a78d6", uat_or_adsr: "#eda100", none: "#4a3aa7" };
const CLASS_LABEL: Record<string, string> = {
  rotorcraft: "Helicopters",
  light: "Light aircraft",
  small: "Small aircraft",
  large: "Large aircraft",
  unknown: "No category",
  other: "Other",
};

function grouped(byClass: Record<string, number>): number[] {
  const order = ["rotorcraft", "light", "small", "large", "unknown", "other"];
  return GROUPS.map((group) => group.classes.reduce((sum, index) => sum + (byClass[order[index]] ?? 0), 0));
}

function section(id: string, title: string, lead: (Node | string)[], body: (Node | null)[]): HTMLElement {
  return el("section", { class: "finding", id }, [
    el("div", { class: "finding-text" }, [el("h2", {}, [title]), ...lead]),
    el("div", { class: "finding-figure" }, body),
  ]);
}

function p(...children: (Node | string)[]): HTMLElement {
  return el("p", {}, children);
}

function big(value: string, text: string): HTMLElement {
  return el("p", { class: "readout" }, [el("span", { class: "num" }, [value]), ` ${text}`]);
}

function caveat(text: string): HTMLElement {
  return el("p", { class: "caveat" }, [text]);
}

function details(summary: string, body: Node): HTMLElement {
  return el("details", { class: "more" }, [el("summary", {}, [summary]), body]);
}

function spotName(spot: Hotspot): string {
  if (spot.nearest_pad) return spot.nearest_pad.name;
  const near = spot.nearest_public;
  return near ? `${num(near.nm, 1)} NM ${near.dir} of ${near.name}` : "Unnamed";
}

export interface ReportActions {
  showOnMap(lat: number, lon: number): void;
}

export interface Answer {
  id: string;
  value: string;
  label: string;
}

/** The five headline numbers, short enough to sit in one row above the map. */
export function answers(findings: Findings, metro: Metro): Answer[] {
  const { q1, q2, q3, q4, q5 } = findings;
  const apt = q2.airports;
  return [
    { id: "traffic", value: num(q3.cruise.average_present, 1), label: "crewed aircraft at drone height at a typical moment" },
    { id: "standoff", value: pct(q4.public_runway_3nm.share_of_low), label: "of low traffic is within 3 NM of a public runway" },
    { id: "rule", value: pct(q1.area.mandatory_share), label: `of ${metro.traced ? "the proposed area" : "Zipline's area"} requires aircraft to broadcast` },
    { id: "links", value: `${pct(q5.single_band_miss_share)} or more`, label: "of low traffic is off the main broadcast band" },
    { id: "reception", value: `${apt.tracked_to_ground} of ${apt.checked}`, label: "airports where public tracking hears to the ground" },
  ];
}

export interface Section {
  id: string;
  /** Short name for the tab. */
  tab: string;
  node: HTMLElement;
}

/** Every panel the drawer can show, in reading order. */
export function buildSections(findings: Findings, actions: ReportActions, metro: Metro): Section[] {
  const { q1, q2, q3, q4, q5, study } = findings;
  const apt = q2.airports;
  const assessment = metro.traced ? "draft assessment" : "assessment";

  // 1. Reception
  const total = q2.cells || 1;
  const near = apt.by_distance[0];
  // The outer airports: beyond 30 NM when at least three airports lie that far out, as around DFW, otherwise beyond 20 NM.
  const outer = (from: number) => apt.by_distance.filter((band) => band.lo_nm >= from).reduce((sum, band) => sum + band.airports, 0);
  const farFrom = outer(30) >= 3 ? 30 : 20;
  const far = apt.by_distance.filter((band) => band.lo_nm >= farFrom);
  const farShare = far.reduce((sum, band) => sum + band.to_ground_share * band.visits, 0) / (far.reduce((sum, band) => sum + band.visits, 0) || 1);
  const named = new Set(metro.named);
  const hub = metro.hub;
  const xMax = Math.max(30, Math.ceil(Math.max(0, ...apt.table.map((row) => row.hub_nm)) / 10) * 10);
  const reception = section(
    "reception",
    "How low public tracking can hear",
    [
      big(`${apt.tracked_to_ground} of ${apt.checked}`, `airports where most arrivals and departures were heard to within ${num(apt.ground_ft)} ft of the ground.`),
      p(
        `Aircraft at an airport are known to reach the ground, so the height at which public tracking last hears them is a direct reading of its floor there. Within 10 NM of ${hub.id} it heard ${pct(near?.to_ground_share)} of them to the runway.` +
          (far.length
            ? ` Beyond ${farFrom} NM that falls to ${pct(farShare)}` +
              (far.every((band) => band.median_of_medians_ft >= 150) ? ", and public tracking typically loses an arrival a few hundred feet up." : ".")
            : ""),
      ),
      p(
        `Across the map, at least ${q2.min_support} different aircraft were heard at drone height in ${pct(q2.heard_cruise_share)} of cells and below 1,200 ft in ${pct(q2.heard_low_share)}. The rest is unproven: it may be quiet, or the receivers may not reach.`,
      ),
      caveat("Every traffic figure below is what this ground network heard. Where its floor is high, low traffic is undercounted. An onboard receiver a few thousand feet from an aircraft hears under very different geometry."),
    ],
    [
      dotPlot(
        apt.table.map((row) => ({
          x: row.hub_nm,
          y: row.to_ground_share,
          size: row.visits,
          label: row.id,
          named: named.has(row.id),
          tip: `${pct(row.to_ground_share)} heard to the ground\n${row.name} (${row.id})\n${num(row.visits)} arrivals and departures\nmedian lowest height ${feet(row.median_lowest_ft)}`,
        })),
        {
          x: `Distance from ${hub.name}, nautical miles`,
          y: "Share of arrivals and departures heard to the ground, by airport. Larger dots have more traffic.",
          xMax,
          yFormat: (value) => pct(value),
          label: `Share of arrivals heard to the ground at each airport against distance from ${hub.id}`,
        },
      ),
      stack(
        [
          { label: "of cells: heard at drone height (below 580 ft)", share: q2.tiers.cruise / total, color: TIER_HEX[0] },
          { label: "heard below 1,200 ft only", share: q2.tiers.low / total, color: TIER_HEX[1] },
          { label: "only heard higher up", share: q2.tiers.higher / total, color: TIER_HEX[2] },
          { label: "nothing heard", share: q2.tiers.none / total, color: TIER_HEX[3], hatch: true },
        ],
        "Share of map cells by how low public tracking was shown to hear",
        (share) => pct(share),
      ),
      details(
        `All ${apt.checked} airports checked`,
        table(
          ["Airport", `From ${hub.id}`, "Visits", "Median lowest", "To the ground"],
          apt.table.map((row) => [`${row.name} (${row.id})`, `${num(row.hub_nm)} NM`, num(row.visits), feet(row.median_lowest_ft), pct(row.to_ground_share)]),
          [1, 2, 3, 4],
        ),
      ),
    ],
  );

  // 2. Rule
  const hasVeil = q1.area.veil_sq_mi > 0;
  const boundary = study.boundary;
  const areaNote = boundary
    ? `Proposed operating area traced from the map in Appendix H of the draft assessment, which publishes no coordinates: ${num(study.area.computed_sq_mi)} sq mi computed against ${num(study.area.stated_sq_mi)} stated. Fitting the map to ${num(boundary.dots_matched)} airports and heliports left a typical error of ${num(boundary.residual_rms_m)} m.`
    : `Operating area drawn from the four corner coordinates in the assessment: ${num(study.area.computed_sq_mi)} sq mi computed against ${num(study.area.stated_sq_mi)} stated. ${study.longitude_correction ?? ""}`;
  const rule = section(
    "rule",
    "Where broadcasting is required",
    [
      big(pct(q1.area.mandatory_share), `of the ${num(q1.area.operating_sq_mi)} sq mi ${metro.traced ? "proposed " : ""}operating area is where an aircraft with an engine-driven electrical system must broadcast ADS-B Out below 1,200 ft.`),
      p(
        (hasVeil
          ? `That is the 30 NM Mode C veil around ${hub.id}, ${num(q1.area.veil_sq_mi)} sq mi of it inside the area.`
          : `That is the Class C airspace around ${hub.name}, ${num(q1.area.class_c_low_sq_mi)} sq mi of it inside the area. There is no Mode C veil here; the FAA draws one only around Class B airports.`) +
          ` Over the other ${pct(q1.area.outside_rule_share)}, a low-flying aircraft is not required to broadcast at all. Zipline's comment on the FAA's Part 108 rule asks for that requirement to cover all crewed aircraft.`,
      ),
      caveat(
        hasVeil
          ? `${pct(q1.traffic.share_where_required)} of the low traffic public tracking heard was inside the veil, but the veil is also where public tracking hears best, so the split overstates the difference.`
          : `${pct(q1.traffic.share_where_required)} of the low traffic public tracking heard was inside that airspace. Public tracking hears best near ${hub.id}, so the split overstates the difference.`,
      ),
    ],
    [
      bars(
        [
          { label: "Share of the area inside the rule", value: q1.area.mandatory_share, display: pct(q1.area.mandatory_share), color: "#a8176b" },
          { label: "Share of heard low traffic inside it", value: q1.traffic.share_where_required ?? 0, display: pct(q1.traffic.share_where_required), color: "#a8176b" },
        ],
        { max: 1, label: "Area and traffic inside the broadcast rule" },
      ),
      p(el("span", { class: "muted" }, [areaNote])),
    ],
  );

  // 3. Traffic
  const cruiseGroups = grouped(q3.cruise.by_class);
  const lowGroups = grouped(q3.low.by_class);
  const cruiseTotal = cruiseGroups.reduce((a, b) => a + b, 0) || 1;
  const lowTotal = lowGroups.reduce((a, b) => a + b, 0) || 1;
  const lights = findings.health.days.map((d) => d.light_aircraft);
  const larges = findings.health.days.map((d) => d.large_aircraft);
  // Airline traffic barely changes from day to day. If it held steady while light aircraft swung, the swing is weather.
  const steadyLarge = Math.max(...larges) <= 1.25 * Math.min(...larges);
  const swingingLight = Math.max(...lights) >= 2 * Math.min(...lights);
  const traffic = section(
    "traffic",
    "Who flies at drone height",
    [
      big(`${num(q3.cruise.average_present, 1)} aircraft`, `on average between 80 and 580 ft, and ${num(q3.low.average_present, 1)} below 1,200 ft, over the whole area.`),
      p(
        `The 80–580 ft band is the 330 ft cruise in the ${assessment} plus and minus 250 ft, the vertical distance in the exemption's encounter-reporting condition. It is a slice of altitude, not a count of encounters. ${num(q3.cruise.distinct_aircraft)} different aircraft passed through it in the week.`,
      ),
      p(
        `Weekdays averaged ${num(q3.cruise.weekday_present ?? 0, 1)} in that band and weekends ${num(q3.cruise.weekend_present ?? 0, 1)}. Helicopters are ${pct(cruiseGroups[0] / cruiseTotal)} of the time at 80–580 ft against ${pct(lowGroups[0] / lowTotal)} below 1,200 ft.`,
      ),
      caveat(
        steadyLarge && swingingLight
          ? `Light-aircraft activity ranged from ${num(Math.min(...lights))} to ${num(Math.max(...lights))} aircraft a day while large aircraft stayed between ${num(Math.min(...larges))} and ${num(Math.max(...larges))}, so the swing is flying weather, not the tracking data.`
          : `Light-aircraft activity ranged from ${num(Math.min(...lights))} to ${num(Math.max(...lights))} aircraft a day and large aircraft from ${num(Math.min(...larges))} to ${num(Math.max(...larges))}. One week is a small sample of flying weather.`,
      ),
    ],
    [
      hourColumns(
        { label: "Below 1,200 ft", color: "#9dbde6", values: q3.low.by_hour_present },
        { label: "80–580 ft", color: "#1d4f91", values: q3.cruise.by_hour_present },
        "average aircraft present, Central Time",
        (value) => num(value, value < 10 && value % 1 !== 0 ? 1 : 0),
      ),
      el("p", { class: "chart-title" }, ["Share of time at 80–580 ft, by aircraft type"]),
      bars(
        GROUPS.map((group, i) => ({
          label: group.label,
          value: cruiseGroups[i] / cruiseTotal,
          display: pct(cruiseGroups[i] / cruiseTotal),
          color: group.color,
          tip: `${pct(cruiseGroups[i] / cruiseTotal)} at 80–580 ft\n${pct(lowGroups[i] / lowTotal)} below 1,200 ft\n${group.label}`,
        })),
        { label: "Aircraft mix at 80 to 580 feet" },
      ),
      details(
        "Day by day",
        table(
          ["UTC day", "Aircraft", "Large", "Light", "Tracked hours"],
          findings.health.days.map((d) => [day(d.date, true), num(d.aircraft), num(d.large_aircraft), num(d.light_aircraft), num(d.tracked_hours)]),
          [1, 2, 3, 4],
        ),
      ),
    ],
  );

  // 4. Stand-off
  const rest = q4.remainder_hours || 1;
  const restGroups = grouped(q4.remainder_by_class);
  const spotRows = q4.hotspots.map((spot, index) => {
    const button = el("button", { type: "button", class: "link-button" }, [spotName(spot)]);
    button.addEventListener("click", () => actions.showOnMap(spot.lat, spot.lon));
    const near = spot.nearest_public;
    const whereText = spot.nearest_pad && near ? `${num(near.nm, 1)} NM ${near.dir} of ${near.name}` : "";
    return [
      String(index + 1),
      el("span", {}, [button, whereText ? el("span", { class: "muted block" }, [whereText]) : null]),
      perDay(spot.minutes_per_day),
      num(spot.aircraft),
      CLASS_LABEL[spot.top_class ?? ""] ?? "n/a",
      hourRange(spot.busiest_3h_start, spot.busiest_3h_start + 3),
    ];
  });
  const standoff = section(
    "standoff",
    "What the runway stand-off leaves",
    [
      big(pct(q4.public_runway_3nm.share_of_low), `of low traffic is within ${num(study.runway_buffer_nm)} NM of a public-use runway, the stand-off in condition 18b of Zipline's exemption.`),
      p(
        `Of the ${pct(1 - (q4.public_runway_3nm.share_of_low ?? 0))} outside it, helicopters are ${pct(restGroups[0] / rest)}.` +
          (restGroups[0] === Math.max(...restGroups) ? " They are the aircraft that work low away from runways, at hospital pads and private heliports." : "") +
          (q4.hotspots[0] ? ` The busiest single spot is ${spotName(q4.hotspots[0])}.` : ""),
      ),
      p(
        `The exemption also tells Zipline to avoid "known areas with increased aviation activity". The table lists the busiest such areas this network can see at 80–580 ft once the runway stand-off is taken out.`,
      ),
      caveat("The stand-off applies \"without suitable mitigations\", so it is not a line Zipline never crosses. Heights are above bare earth; a helicopter over a rooftop pad reads high by the height of the building."),
    ],
    [
      el("p", { class: "chart-title" }, ["Low traffic outside the public-runway stand-off, by where it is"]),
      bars(
        [
          { label: "Near a private-use runway", note: "within 3 NM", value: q4.private_runway_3nm.hours / rest, display: pct(q4.private_runway_3nm.hours / rest) },
          { label: "Near a medical heliport", note: "within 1 NM", value: q4.hospital_1nm.hours / rest, display: pct(q4.hospital_1nm.hours / rest) },
          { label: "Near another heliport", note: "within 1 NM", value: q4.other_heliport_1nm.hours / rest, display: pct(q4.other_heliport_1nm.hours / rest) },
          { label: "Everywhere else", value: q4.elsewhere.hours / rest, display: pct(q4.elsewhere.hours / rest) },
        ],
        { max: 1, label: "Low traffic outside the stand-off by location" },
      ),
      el("p", { class: "chart-title" }, ["Busiest areas at 80–580 ft away from public runways"]),
      spotRows.length
        ? el("div", { class: "table-scroll" }, [table(["", "Area", "Aircraft time", "Aircraft", "Mostly", "Busiest"], spotRows, [2, 3])])
        : el("p", { class: "muted" }, ["No area away from public runways had ten aircraft-minutes from three aircraft in a map cell."]),
      el("p", { class: "muted" }, [`${num(q4.medical_heliports)} medical heliports and ${num(q4.heliports)} heliports of any kind sit inside the operating area.${spotRows.length ? " Select an area to see it on the map." : ""}`]),
    ],
  );

  // 5. Links
  const link = (key: string) => q5.by_link[key]?.share ?? 0;
  const none = link("tisb_only") + link("mlat_only");
  const classRows = ["rotorcraft", "light", "small", "large"].filter((name) => q5.by_class[name]);
  const links = section(
    "links",
    "Which radio link they were on",
    [
      big(`at least ${pct(q5.single_band_miss_share, 1)}`, "of low traffic would be missed by a receiver that listens on 1090 MHz alone."),
      p(
        `${(q5.uat_share ?? 0) > (q5.dual_band_miss_share ?? 0) ? "Most" : "Part"} of that is aircraft broadcasting on 978 MHz (UAT) instead, which is ${pct(q5.by_class.light?.uat_or_adsr ?? 0)} of light-aircraft time. A receiver on both bands, as Zipline recommends in its Part 108 comment, would miss at least ${pct(q5.dual_band_miss_share, 1)}: aircraft seen only by radar or by multilateration, with no position broadcast.`,
      ),
      caveat("Both figures are floors. Aircraft with no transponder never enter public tracking, and the network's ways of seeing non-broadcasters work worst where its reception is weakest."),
    ],
    [
      stack(
        [
          { label: "1090 MHz ADS-B", share: link("adsb_1090"), color: LINK_COLORS.adsb_1090 },
          { label: "978 MHz UAT, seen by rebroadcast", share: link("uat_or_adsr"), color: LINK_COLORS.uat_or_adsr },
          { label: "no position broadcast", share: none, color: LINK_COLORS.none },
        ],
        "Share of low traffic by radio link",
        (share) => pct(share, 1),
      ),
      el("p", { class: "chart-title" }, ["Share of each type's low time on 978 MHz"]),
      bars(
        classRows.map((name) => ({
          label: CLASS_LABEL[name],
          value: q5.by_class[name].uat_or_adsr ?? 0,
          display: pct(q5.by_class[name].uat_or_adsr ?? 0, 1),
          color: LINK_COLORS.uat_or_adsr,
        })),
        { label: "Share of low time on 978 MHz by aircraft type" },
      ),
      p(
        el("span", { class: "muted" }, [
          `Inside the ${hasVeil ? "veil" : "Class C airspace"} ${pct(q5.inside_rule.uat_share, 1)} of low time was on UAT and ${pct(q5.inside_rule.dual_band_miss_share, 1)} had no broadcast; outside it, ${pct(q5.outside_rule.uat_share, 1)} and ${pct(q5.outside_rule.dual_band_miss_share, 1)}.`,
        ]),
      ),
    ],
  );

  // Method
  const cal = findings.calibration;
  const runs = findings.sensitivity.runs;
  const sensRow = (label: string, pick: (run: (typeof runs)["nominal"]) => string) => [label, pick(runs.minus), pick(runs.nominal), pick(runs.plus)];
  const method = section(
    "method",
    "How the heights were checked",
    [
      big(`${num(cal.chosen?.p90_abs_ft ?? 0)} ft`, `is the error that 90% of on-runway height reports fall inside, with a median of ${num(cal.chosen?.p50_ft ?? 0)} ft.`),
      p(
        `Height above ground is geometric altitude, corrected for the geoid, minus terrain. It is checked against ${num(cal.runway_matches ?? 0)} reports from aircraft on the ground within 200 m of a runway end of known elevation. ${pct(cal.agl_geometric_share, 1)} of airborne points used geometric altitude and ${pct(cal.agl_barometric_share, 1)} fell back to barometric.`,
      ),
      p(
        findings.sensitivity.inconclusive.length
          ? `Moving every band up or down ${num(findings.sensitivity.shift_ft)} ft changes ${findings.sensitivity.inconclusive.length} of the headline shares by more than 5 points; those are marked inconclusive.`
          : `Moving every band up or down ${num(findings.sensitivity.shift_ft)} ft changes none of the headline shares by more than 5 points.`,
      ),
      p(el("a", { href: "./METHODS.md" }, ["Every definition"]), " and ", el("a", { href: "./LIMITS.md" }, ["what the numbers do not mean"]), "."),
    ],
    [
      histogram(
        (cal.histogram ?? []).map((bin) => ({ lo: bin.lo_ft, n: bin.n })),
        25,
        "Reported height minus runway elevation, feet",
      ),
      el("p", { class: "chart-title" }, [`Headline shares with every band moved ${num(findings.sensitivity.shift_ft)} ft`]),
      el("div", { class: "table-scroll" }, [
        table(
          ["", "100 ft lower", "As measured", "100 ft higher"],
          [
            sensRow("Low traffic inside the runway stand-off", (run) => pct(run.public_runway_share)),
            sensRow("Missed on 1090 MHz alone", (run) => pct(run.single_band_miss_share, 1)),
            sensRow("Missed on both bands", (run) => pct(run.dual_band_miss_share, 1)),
            sensRow("Helicopter share at drone height", (run) => pct(run.rotorcraft_share_of_cruise)),
          ],
          [1, 2, 3],
        ),
      ]),
    ],
  );

  const limits = el("section", { class: "prose", id: "limits" }, [
    el("h2", {}, ["What this does not show"]),
    el("ul", {}, [
      el("li", {}, ["It is not a collision-risk estimate. It counts aircraft time in an altitude band; it does not model encounters."]),
      el("li", {}, ["It does not track Zipline aircraft or sites. The 10-mile brief describes the air around any point you choose."]),
      el("li", {}, ["It cannot see aircraft with no transponder, or anything below the network's floor."]),
      el("li", {}, [
        boundary
          ? `It covers one week and one proposed operating area, traced from a map in a draft assessment. The final boundary may differ, and the traced edges can be off by a hundred metres or more.`
          : "It covers one week and one published operating area. Austin has its own page; the other pending Texas metros are not included.",
      ]),
      el("li", {}, ["It says nothing about what a receiver on the aircraft would hear. That comparison needs Zipline's own logs."]),
    ]),
  ]);

  const dfwAssessment = el("a", { href: "https://www.faa.gov/uas/advanced_operations/nepa_and_drones/20251223_Zipline_Final_DFW_EA_508_signed.pdf" }, ["FAA final environmental assessment, Dallas–Fort Worth"]);
  const texasAssessment = el("a", { href: "https://www.faa.gov/uas/advanced_operations/nepa_and_drones/20260911_Zipline_Texas_Metros_Draft_EA_CLEAN_ADA_Signed.pdf" }, ["FAA draft environmental assessment, Texas metros"]);
  const assessments = metro.traced
    ? [
        el("li", {}, [texasAssessment, ", 11 September 2026: the study area map (Appendix H), its stated area (Table 2.2-1), and the 330 ft cruise. A draft, not a final decision."]),
        el("li", {}, [dfwAssessment, ": the 10-mile site radius used for the brief."]),
      ]
    : [el("li", {}, [dfwAssessment, ": operating area (footnote 12), 330 ft cruise, 400 ft cap."])];
  const sources = el("section", { class: "prose", id: "sources" }, [
    el("h2", {}, ["Sources"]),
    el("ul", {}, [
      ...assessments,
      el("li", {}, [
        el("a", { href: "https://downloads.regulations.gov/FAA-2020-0499-0060/attachment_1.pdf" }, ["Exemption No. 19111F"]),
        ": the 3 NM runway stand-off and the 250 ft encounter-reporting distance.",
        metro.traced ? " Its conditions are not tied to a metro; they cover every aircraft in Zipline's operations specifications." : "",
      ]),
      el("li", {}, [el("a", { href: "https://downloads.regulations.gov/FAA-2025-1908-2767/attachment_1.pdf" }, ["Zipline's comment on the Part 108 proposal"]), ", 6 October 2025: electronic conspicuity and dual-band ADS-B In."]),
      el("li", {}, [el("a", { href: "https://github.com/adsblol/globe_history_2026" }, ["adsb.lol globe history"]), ", Open Database License. Thanks to adsb.lol and the people who run its receivers."]),
      el("li", {}, ["FAA ", el("a", { href: "https://services6.arcgis.com/ssFJjBXIUyZDrSYZ/arcgis/rest/services/Class_Airspace/FeatureServer/0" }, ["Class Airspace"]), " and ", el("a", { href: "https://www.faa.gov/air_traffic/flight_info/aeronav/aero_data/NASR_Subscription/" }, ["NASR airport data"]), ", 1 October 2026 cycle."]),
      el("li", {}, [el("a", { href: "https://www.usgs.gov/3d-elevation-program" }, ["USGS 3DEP elevation"]), ", ", el("a", { href: "https://geodesy.noaa.gov/GEOID/GEOID18/" }, ["NOAA GEOID18"]), ", and hourly METARs from the Iowa Environmental Mesonet."]),
      el("li", {}, ["Base map: ", el("a", { href: "https://openfreemap.org/" }, ["OpenFreeMap"]), " and OpenStreetMap contributors."]),
    ]),
  ]);

  const [first, last] = study.window;
  const about = el("section", { class: "prose", id: "about" }, [
    el("h2", {}, [`Who shares the low sky with Zipline over ${metro.name}`]),
    p(
      boundary
        ? `Zipline's delivery aircraft cruise at 330 ft. For ${metro.name}, the FAA's draft assessment of September 2026 proposes an operating area but shows it only as a map, so the outline here is traced from that map. Planes and helicopters use the same air, and Zipline has asked the FAA to require them all to broadcast their position.`
        : `Zipline's delivery aircraft cruise at 330 ft inside a box the FAA has published for ${metro.name}. Planes and helicopters use the same air, and Zipline has asked the FAA to require them all to broadcast their position.`,
    ),
    p(
      `This survey takes one week of public tracking data and measures where crewed aircraft fly low in that ${boundary ? "area" : "box"}, which of them broadcast, and how low a ground receiver network can actually hear them.`,
    ),
    boundary
      ? p(
          `The outline was traced by matching ${num(boundary.dots_matched)} airport and heliport symbols on the map to their FAA coordinates, then fitting straight lines to the red boundary. The fit is off by ${num(boundary.residual_rms_m)} m at a typical airport (root mean square) and ${num(boundary.residual_max_m)} m at most, and the corners move by up to ${num(boundary.corner_shift_other_projections_m)} m between map projections that fit equally well. The traced area is ${num(study.area.computed_sq_mi)} sq mi against ${num(study.area.stated_sq_mi)} stated; moving every edge by one pixel of the map (${num(boundary.metres_per_pixel)} m) would give ${num(boundary.area_if_edges_move_1px_sq_mi[0])} to ${num(boundary.area_if_edges_move_1px_sq_mi[1])}. It is a draft boundary, not a final decision.`,
        )
      : null,
    p(
      el("span", { class: "muted" }, [
        `${day(first, true)} to ${day(last, true)} 2026, ${num(findings.window.hours)} hours. ${num(findings.calibration.points ?? 0)} position reports over ${num(q1.area.operating_sq_mi)} sq mi. Based on public data only.`,
      ]),
    ),
    p("Click the map for a brief on the air within 10 miles of any point. The numbers along the top open each finding with its chart and its caveat."),
  ]);
  return [
    { id: "traffic", tab: "Who flies low", node: traffic },
    { id: "standoff", tab: "Runways", node: standoff },
    { id: "rule", tab: "Broadcast rule", node: rule },
    { id: "links", tab: "Radio band", node: links },
    { id: "reception", tab: "Tracking limits", node: reception },
    { id: "method", tab: "Method", node: method },
    { id: "limits", tab: "Limits", node: limits },
    { id: "sources", tab: "Sources", node: sources },
    { id: "about", tab: "About", node: about },
  ];
}
