import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { H3HexagonLayer, TripsLayer } from "@deck.gl/geo-layers";
import { cellsToMultiPolygon, cellToBoundary } from "h3-js";
import type { FeatureCollection } from "geojson";
import { aircraftIn, circle, GROUPS, groupOf, offBandShare, seconds, type Cell, type Selection } from "./data";
import { feet, pct, perDay } from "./format";
import { hideTip, showTip } from "./tip";
import type { Findings, TrackFile, View } from "./types";

const STYLE = "https://tiles.openfreemap.org/styles/positron";
type RGB = [number, number, number];
type RGBA = [number, number, number, number];

export const TRAFFIC_RAMP: RGB[] = [[227, 236, 247], [157, 189, 230], [79, 134, 204], [29, 79, 145], [12, 44, 87]];
export const LINK_RAMP: RGB[] = [[246, 234, 210], [233, 185, 73], [184, 110, 20], [111, 59, 6]];
export const TIER_COLORS: RGB[] = [[15, 107, 92], [111, 179, 164], [221, 228, 224]];
const TOO_FEW: RGBA = [190, 199, 195, 70];
const CLEAR: RGBA = [0, 0, 0, 0];
/** Share at which the "not on 1090 MHz" scale tops out. */
export const LINK_CAP = 0.5;

function ramp(stops: RGB[], t: number, alpha = 205): RGBA {
  const u = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(u));
  const f = u - i;
  const a = stops[i];
  const b = stops[i + 1];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, alpha];
}

export function rampCss(stops: RGB[]): string {
  return `linear-gradient(90deg, ${stops.map((s) => `rgb(${s.join(",")})`).join(", ")})`;
}

function hexFromRgb(color: string): RGB {
  const n = Number.parseInt(color.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** The operating area's outline, with a small margin, as [west, south, east, north]. */
function areaBounds(layers: FeatureCollection): [number, number, number, number] {
  const area = layers.features.find((feature) => feature.properties?.layer === "operating_area");
  const ring = area?.geometry.type === "Polygon" ? area.geometry.coordinates[0] : [];
  if (!ring.length) return [-98.0, 32.16, -95.9, 33.52];
  const lons = ring.map((point) => point[0]);
  const lats = ring.map((point) => point[1]);
  const pad = 0.02;
  return [Math.min(...lons) - pad, Math.min(...lats) - pad, Math.max(...lons) + pad, Math.max(...lats) + pad];
}
/** Leave the floating panels clear: header above, filters on the left, replay below. */
function fit(container: HTMLElement): { padding: { top: number; bottom: number; left: number; right: number } } {
  const wide = container.clientWidth > 820;
  return { padding: wide ? { top: 150, bottom: 70, left: 330, right: 28 } : { top: 170, bottom: 150, left: 14, right: 14 } };
}

export interface MapState extends Selection {
  view: View;
}

export interface MapHandle {
  setState(state: MapState): void;
  setPin(lat: number, lon: number, miles: number): void;
  clearPin(): void;
  flyTo(lat: number, lon: number, zoom?: number): void;
  setOverlay(name: OverlayName, on: boolean): void;
  setReplay(file: TrackFile | null, time: number): void;
  /** Minutes a day at which the traffic scale tops out for the band in view. */
  trafficCap(band: Selection["band"]): number;
}

export type OverlayName = "airspace" | "standoff" | "facilities" | "busy";

const OVERLAY_LAYERS: Record<OverlayName, string[]> = {
  airspace: ["veil-line", "class-b-line", "class-c-line", "class-d-line"],
  standoff: ["standoff-fill", "standoff-line"],
  facilities: ["runways", "airports", "airport-labels", "heliports"],
  busy: ["busy-line"],
};

export function createMap(
  container: HTMLElement,
  cells: Cell[],
  layers: FeatureCollection,
  findings: Findings,
  onPick: (lat: number, lon: number) => void,
): MapHandle {
  const days = findings.window.hours / 24;
  const areaBox = areaBounds(layers);
  const map = new maplibregl.Map({
    container,
    style: STYLE,
    bounds: areaBox,
    fitBoundsOptions: fit(container),
    attributionControl: { compact: true },
    cooperativeGestures: true,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "nautical" }), "bottom-right");

  const perDayCap = (band: Selection["band"]) => {
    const values = cells
      .map((cell) => (band === "cruise" ? cell.cruiseTotal : cell.lowTotal) / 60 / days)
      .filter((value) => value > 0)
      .sort((a, b) => a - b);
    return values.length ? values[Math.floor(0.98 * (values.length - 1))] : 1;
  };
  const caps = { cruise: perDayCap("cruise"), low: perDayCap("low") };

  let state: MapState = { view: "traffic", band: "cruise", group: "all", from: 0, to: 23 };
  let replay: TrackFile | null = null;
  let replayTime = 0;
  const overlay = new MapboxOverlay({ interleaved: true, layers: [] });
  let ready = false;

  function fill(cell: Cell): RGBA {
    if (state.view === "reception") return cell.tier < 3 ? [...TIER_COLORS[cell.tier], 200] : CLEAR;
    if (state.view === "link") {
      const share = offBandShare(cell);
      if (share == null) return CLEAR;
      if (cell.lowAircraft < findings.q3.min_distinct_for_a_cell) return TOO_FEW;
      return ramp(LINK_RAMP, share / LINK_CAP);
    }
    const value = seconds(cell, state);
    if (value <= 0) return CLEAR;
    if (aircraftIn(cell, state.band) < findings.q3.min_distinct_for_a_cell) return TOO_FEW;
    return ramp(TRAFFIC_RAMP, Math.sqrt(value / 60 / days / caps[state.band]));
  }

  function describe(cell: Cell): string {
    if (state.view === "reception") {
      const tier = [
        "Heard at drone height",
        "Heard below 1,200 ft, not at drone height",
        "Only heard higher up",
        "Nothing heard",
      ][cell.tier];
      return cell.lowestFt == null ? tier : `${tier}\nThird-lowest aircraft heard at ${feet(cell.lowestFt)}`;
    }
    if (state.view === "link") {
      const share = offBandShare(cell);
      if (share == null) return "No low traffic heard here";
      if (cell.lowAircraft < findings.q3.min_distinct_for_a_cell) return "Too few aircraft to give a share";
      return `${pct(share)} not on 1090 MHz\nof low time, from ${cell.lowAircraft} aircraft`;
    }
    const value = seconds(cell, state);
    if (value <= 0) return "Nothing heard here for this selection";
    const band = state.band === "cruise" ? "80–580 ft" : "below 1,200 ft";
    const count = aircraftIn(cell, state.band);
    return `${perDay(value / 60 / days)}\nof aircraft time ${band}\n${count} aircraft in the week${count < 3 ? " (too few to colour)" : ""}`;
  }

  function draw(): void {
    if (!ready) return;
    const hexes = new H3HexagonLayer<Cell>({
      id: "cells",
      data: cells,
      getHexagon: (cell) => cell.h,
      getFillColor: fill,
      stroked: false,
      extruded: false,
      highPrecision: false,
      opacity: replay ? 0.25 : 1,
      pickable: true,
      // Read by the interleaved overlay: draw the cells under the airspace lines.
      ...({ beforeId: "standoff-fill" } as object),
      onHover: (info) => {
        if (info.object && !replay) {
          const box = container.getBoundingClientRect();
          showTip(describe(info.object), box.left + info.x, box.top + info.y);
        } else hideTip();
      },
      updateTriggers: { getFillColor: [state.view, state.band, state.group, state.from, state.to] },
    });
    const list: unknown[] = [hexes];
    if (replay) {
      list.push(
        new TripsLayer<TrackFile["tracks"][number]>({
          id: "replay",
          data: replay.tracks,
          getPath: (track) => {
            const path: [number, number][] = [];
            for (let i = 0; i < track.p.length; i += 4) path.push([track.p[i + 1], track.p[i + 2]]);
            return path;
          },
          getTimestamps: (track) => {
            const times: number[] = [];
            for (let i = 0; i < track.p.length; i += 4) times.push(track.p[i]);
            return times;
          },
          getColor: (track) => hexFromRgb(GROUPS[Math.max(0, groupOf(track.c))].color),
          currentTime: replayTime,
          trailLength: 420,
          fadeTrail: true,
          widthMinPixels: 2.5,
          capRounded: true,
          jointRounded: true,
        }),
      );
    }
    overlay.setProps({ layers: list as never[] });
  }

  map.on("load", () => {
    const pattern = document.createElement("canvas");
    pattern.width = 12;
    pattern.height = 12;
    const pen = pattern.getContext("2d");
    if (pen) {
      pen.strokeStyle = "rgba(85, 97, 111, 0.55)";
      pen.lineWidth = 1.4;
      for (const offset of [-12, 0, 12]) {
        pen.beginPath();
        pen.moveTo(offset, 12);
        pen.lineTo(offset + 12, 0);
        pen.stroke();
      }
      map.addImage("hatch", pen.getImageData(0, 0, 12, 12), { pixelRatio: 2 });
    }
    const ring = (h: string) => {
      const points = cellToBoundary(h).map(([lat, lon]) => [lon, lat]);
      points.push(points[0]);
      return points;
    };
    map.addSource("unheard", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: cells
          .filter((cell) => cell.tier === 3)
          .map((cell) => ({ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(cell.h)] } })),
      },
    });
    map.addLayer({ id: "unheard", type: "fill", source: "unheard", layout: { visibility: "none" }, paint: { "fill-pattern": "hatch" } });
    map.addSource("airspace", { type: "geojson", data: layers });
    const where = (name: string): maplibregl.FilterSpecification => ["==", ["get", "layer"], name];
    map.addLayer({ id: "standoff-fill", type: "fill", source: "airspace", filter: where("runway_buffer"), paint: { "fill-color": "#1b2536", "fill-opacity": 0.05 } });
    map.addLayer({ id: "standoff-line", type: "line", source: "airspace", filter: where("runway_buffer"), paint: { "line-color": "#1b2536", "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.7 } });
    map.addLayer({ id: "class-d-line", type: "line", source: "airspace", filter: where("class_d"), layout: { visibility: "none" }, paint: { "line-color": "#1d4f91", "line-width": 1.2, "line-dasharray": [4, 3] } });
    map.addLayer({ id: "class-b-line", type: "line", source: "airspace", filter: where("class_b"), layout: { visibility: "none" }, paint: { "line-color": "#1d4f91", "line-width": 1.6 } });
    map.addLayer({ id: "class-c-line", type: "line", source: "airspace", filter: where("class_c"), layout: { visibility: "none" }, paint: { "line-color": "#1d4f91", "line-width": 1.6 } });
    map.addLayer({ id: "veil-line", type: "line", source: "airspace", filter: where("veil"), layout: { visibility: "none" }, paint: { "line-color": "#a8176b", "line-width": 1.8 } });
    map.addLayer({ id: "area-line", type: "line", source: "airspace", filter: where("operating_area"), paint: { "line-color": "#1b2536", "line-width": 2 } });
    map.addSource("busy", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: findings.q4.hotspots.map((spot, index) => ({
          type: "Feature",
          properties: { rank: index + 1 },
          geometry: { type: "MultiPolygon", coordinates: cellsToMultiPolygon(spot.cells, true) },
        })),
      },
    });
    map.addLayer({ id: "busy-line", type: "line", source: "busy", layout: { visibility: "none" }, paint: { "line-color": "#eb6834", "line-width": 2.2 } });
    map.addLayer({ id: "runways", type: "line", source: "airspace", filter: where("runway"), minzoom: 8.5, layout: { visibility: "none" }, paint: { "line-color": "#1b2536", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 13, 5] } });
    map.addLayer({
      id: "heliports",
      type: "circle",
      source: "airspace",
      filter: where("heliport"),
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, 2, 11, 5],
        "circle-color": ["case", ["get", "medical"], "#a8176b", "#ffffff"],
        "circle-stroke-width": 1.2,
        "circle-stroke-color": ["case", ["get", "medical"], "#ffffff", "#1b2536"],
      },
    });
    map.addLayer({
      id: "airports",
      type: "circle",
      source: "airspace",
      filter: where("airport"),
      layout: { visibility: "none" },
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 7, ["case", ["get", "public"], 3.5, 2], 11, ["case", ["get", "public"], 7, 4.5]],
        "circle-color": ["case", ["get", "public"], "#1b2536", "#ffffff"],
        "circle-stroke-width": 1.2,
        "circle-stroke-color": ["case", ["get", "public"], "#ffffff", "#1b2536"],
      },
    });
    map.addLayer({
      id: "airport-labels",
      type: "symbol",
      source: "airspace",
      filter: ["all", ["==", ["get", "layer"], "airport"], ["get", "public"]] as maplibregl.FilterSpecification,
      minzoom: 8.2,
      layout: { visibility: "none", "text-field": ["get", "id"], "text-size": 11, "text-offset": [0, 0.9], "text-anchor": "top", "text-font": ["Noto Sans Regular"] },
      paint: { "text-color": "#1b2536", "text-halo-color": "#ffffff", "text-halo-width": 1.4 },
    });
    map.addSource("pin", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "pin-fill", type: "fill", source: "pin", filter: ["==", ["geometry-type"], "Polygon"], paint: { "fill-color": "#ffffff", "fill-opacity": 0.12 } });
    map.addLayer({ id: "pin-line", type: "line", source: "pin", filter: ["==", ["geometry-type"], "Polygon"], paint: { "line-color": "#1b2536", "line-width": 2.4 } });
    map.addLayer({ id: "pin-dot", type: "circle", source: "pin", filter: ["==", ["geometry-type"], "Point"], paint: { "circle-radius": 5, "circle-color": "#1b2536", "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 } });
    map.addControl(overlay);
    ready = true;
    for (const [name, on] of Object.entries(pending)) apply(name as OverlayName, on);
    map.setLayoutProperty("unheard", "visibility", state.view === "reception" ? "visible" : "none");
    draw();
    for (const id of ["airports", "heliports"]) {
      map.on("mousemove", id, (event) => {
        const props = event.features?.[0]?.properties;
        if (!props) return;
        const kind = id === "heliports" ? (props.medical ? "Medical heliport" : "Heliport") : props.public ? "Public-use airport" : "Private-use airport";
        const heard = props.visits ? `\nArrivals heard to ${feet(props.lowest_ft)} (median of ${props.visits})` : "";
        showTip(`${props.name}\n${kind}${heard}`, event.originalEvent.clientX, event.originalEvent.clientY);
      });
      map.on("mouseleave", id, hideTip);
    }
  });
  map.on("click", (event) => {
    hideTip();
    onPick(event.lngLat.lat, event.lngLat.lng);
  });
  map.on("mouseout", hideTip);

  // Keep the whole operating area in view as the layout settles or the window
  // changes size, until the reader moves the map themselves.
  let moved = false;
  map.on("movestart", (event) => {
    if ((event as { originalEvent?: Event }).originalEvent) moved = true;
  });
  new ResizeObserver(() => {
    map.resize();
    if (!moved) map.fitBounds(areaBox, { ...fit(container), duration: 0 });
  }).observe(container);

  const pending: Partial<Record<OverlayName, boolean>> = {};
  function apply(name: OverlayName, on: boolean): void {
    for (const id of OVERLAY_LAYERS[name]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    }
  }

  return {
    setState(next) {
      state = next;
      if (ready) map.setLayoutProperty("unheard", "visibility", next.view === "reception" ? "visible" : "none");
      draw();
    },
    setPin(lat, lon, miles) {
      const source = map.getSource("pin") as maplibregl.GeoJSONSource | undefined;
      source?.setData({
        type: "FeatureCollection",
        features: [
          { type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [circle(lat, lon, miles)] } },
          { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lon, lat] } },
        ],
      });
    },
    clearPin() {
      (map.getSource("pin") as maplibregl.GeoJSONSource | undefined)?.setData({ type: "FeatureCollection", features: [] });
    },
    flyTo(lat, lon, zoom = 10) {
      moved = true;
      map.flyTo({ center: [lon, lat], zoom, essential: false });
    },
    setOverlay(name, on) {
      pending[name] = on;
      if (ready) apply(name, on);
    },
    setReplay(file, time) {
      const changed = (file == null) !== (replay == null);
      replay = file;
      replayTime = time;
      if (changed) hideTip();
      draw();
    },
    trafficCap: (band) => caps[band],
  };
}
