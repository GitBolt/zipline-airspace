import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Feature, FeatureCollection, Polygon, Position } from "geojson";
import { day, el, num, pct } from "./format";
import { STYLE } from "./map";
import { markSwitch, metroHref, type Metro } from "./metros";
import type { Findings } from "./types";

export interface LoadedMetro {
  metro: Metro;
  findings: Findings;
  layers: FeatureCollection;
}

function ringOf(layers: FeatureCollection): Position[] {
  const area = layers.features.find((feature) => feature.properties?.layer === "operating_area");
  return area?.geometry.type === "Polygon" ? area.geometry.coordinates[0] : [];
}

function centre(ring: Position[]): [number, number] {
  const lons = ring.map((point) => point[0]);
  const lats = ring.map((point) => point[1]);
  return [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
}

function open(metro: Metro): void {
  markSwitch();
  window.location.href = metroHref(metro);
}

/** Leave room for the card: on the left when wide, along the bottom when narrow. */
function padding(container: HTMLElement): maplibregl.PaddingOptions {
  const wide = container.clientWidth > 820;
  return wide
    ? { top: 90, bottom: 40, left: 470, right: 40 }
    : { top: 80, bottom: Math.round(container.clientHeight * 0.62), left: 24, right: 24 };
}

function option({ metro, findings }: LoadedMetro): HTMLElement {
  const { q1, q3, q4 } = findings;
  const link = el("a", { class: "metro-option", href: metroHref(metro) }, [
    el("span", { class: "metro-option-head" }, [
      el("strong", {}, [metro.name]),
      el("span", { class: "tag" }, [metro.traced ? "Draft area, traced" : "Approved area"]),
    ]),
    el("span", { class: "metro-option-stats" }, [
      el("span", {}, [
        el("span", { class: "num" }, [num(q3.cruise.average_present, 1)]),
        el("span", { class: "muted" }, ["crewed aircraft at drone height at a typical moment"]),
      ]),
      el("span", {}, [
        el("span", { class: "num" }, [pct(q4.public_runway_3nm.share_of_low)]),
        el("span", { class: "muted" }, ["of low traffic within 3 NM of a public runway"]),
      ]),
      el("span", {}, [
        el("span", { class: "num" }, [pct(q1.area.mandatory_share)]),
        el("span", { class: "muted" }, ["of the area requires aircraft to broadcast"]),
      ]),
    ]),
    el("span", { class: "metro-option-foot" }, [
      el("span", { class: "muted" }, [`${num(q1.area.operating_sq_mi)} sq mi`]),
      el("span", { class: "go" }, ["Open the map →"]),
    ]),
  ]);
  link.addEventListener("click", markSwitch);
  return link;
}

/** Every operating area on one map, with a card that explains the survey and opens a metro. */
export function renderOverview(loaded: LoadedMetro[]): HTMLElement {
  const [from, to] = loaded[0].findings.study.window;
  const sameWeek = loaded.every((item) => item.findings.study.window.join() === loaded[0].findings.study.window.join());
  const card = el("section", { class: "overview-card", "aria-label": "Choose a metro" }, [
    el("h2", {}, ["Who shares the low sky with Zipline?"]),
    el("p", {}, [
      "Zipline's delivery drones cruise at 330 ft. Helicopters and small planes fly low in the same air. This survey uses public flight tracking to show where those crewed aircraft fly at drone height, whether they broadcast their position, and where public tracking cannot see them.",
    ]),
    el("p", { class: "muted" }, [
      sameWeek
        ? `The same week for every metro: ${day(from)} to ${day(to)} ${to.slice(0, 4)}. Pick one to open its map.`
        : "Pick a metro to open its map.",
    ]),
    el("div", { class: "metro-options" }, loaded.map(option)),
  ]);

  const container = el("div", { class: "map" });
  const root = el("div", { class: "overview" }, [container, card]);

  const features: Feature<Polygon>[] = loaded
    .map(({ metro, layers }) => ({ metro, ring: ringOf(layers) }))
    .filter(({ ring }) => ring.length > 0)
    .map(({ metro, ring }) => ({ type: "Feature", properties: { key: metro.key }, geometry: { type: "Polygon", coordinates: [ring] } }));
  const all = features.flatMap((feature) => feature.geometry.coordinates[0]);
  const bounds: [number, number, number, number] = [
    Math.min(...all.map((p) => p[0])),
    Math.min(...all.map((p) => p[1])),
    Math.max(...all.map((p) => p[0])),
    Math.max(...all.map((p) => p[1])),
  ];

  // The map needs its container in the page to measure it.
  queueMicrotask(() => {
    const map = new maplibregl.Map({
      container,
      style: STYLE,
      bounds,
      fitBoundsOptions: { padding: padding(container) },
      attributionControl: { compact: true },
      cooperativeGestures: true,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.on("load", () => {
      map.addSource("areas", { type: "geojson", data: { type: "FeatureCollection", features } });
      map.addLayer({ id: "area-fill", type: "fill", source: "areas", paint: { "fill-color": "#1d4f91", "fill-opacity": 0.1 } });
      map.addLayer({ id: "area-line", type: "line", source: "areas", paint: { "line-color": "#1b2536", "line-width": 2 } });
      map.on("mouseenter", "area-fill", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "area-fill", () => (map.getCanvas().style.cursor = ""));
      map.on("click", "area-fill", (event) => {
        const key = event.features?.[0]?.properties?.key;
        const hit = loaded.find((item) => item.metro.key === key);
        if (hit) open(hit.metro);
      });
    });
    for (const { metro, layers } of loaded) {
      const ring = ringOf(layers);
      if (!ring.length) continue;
      const label = el("button", { type: "button", class: "area-label" }, [metro.label]);
      label.addEventListener("click", () => open(metro));
      new maplibregl.Marker({ element: label }).setLngLat(centre(ring)).addTo(map);
    }
    new ResizeObserver(() => map.fitBounds(bounds, { padding: padding(container), duration: 0 })).observe(container);
  });

  return root;
}
