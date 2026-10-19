"""Web payloads. Every number on the site is read from these files."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
from shapely.geometry import mapping, shape

from survey.constants import REPLAY_DATE, TIMEZONE
from survey.geometry import as_feature, feature_collection
from survey.metrics import CLASSES
from survey.names import nice_name

REPLAY_STEP_S = 10.0
REPLAY_BREAK_S = 60.0
REPLAY_TOP_FT = 1500.0
PRIVACY_FLAGS = 1 | 4 | 8  # military, PIA, LADD


def _feature(geom, props: dict, tolerance: float = 0.004) -> dict | None:
    if geom is None or geom.is_empty:
        return None
    simple = geom.simplify(tolerance, preserve_topology=True)
    if simple.is_empty:
        return None
    return as_feature(simple, props)


def write_layers(masks: dict, layers_dir: Path, out: Path, reception: dict | None = None) -> None:
    features = []
    for key, props, tol in (
        ("area", {"layer": "operating_area", "label": "Operating area"}, 0.0),
        ("veil", {"layer": "veil", "label": "Mode C veil"}, 0.002),
        ("class_b_surface", {"layer": "class_b", "label": "Class B surface area"}, 0.002),
        ("class_c_surface", {"layer": "class_c", "label": "Class C surface area"}, 0.002),
        ("class_d_surface", {"layer": "class_d", "label": "Class D surface area"}, 0.002),
        ("public_runway", {"layer": "runway_buffer", "label": "3 NM from a public-use runway"}, 0.003),
    ):
        feature = _feature(masks.get(key), props, tol)
        if feature:
            features.append(feature)
    area = masks["area"]
    heard = {row["id"]: row for row in (reception or {}).get("table", [])}
    for feature in json.loads((layers_dir / "airports.geojson").read_text())["features"]:
        geom = shape(feature["geometry"])
        if not area.covers(geom):
            continue
        props = feature["properties"]
        row = heard.get(props["id"])
        out_props = {
            "layer": "airport",
            "public": props.get("use") == "PU",
            "id": props.get("id"),
            "name": nice_name(props.get("name")),
        }
        if row:
            out_props["visits"] = row["visits"]
            out_props["lowest_ft"] = round(row["median_lowest_ft"])
            out_props["to_ground"] = round(row["to_ground_share"], 3)
        features.append({"type": "Feature", "properties": out_props, "geometry": mapping(geom)})
    for feature in json.loads((layers_dir / "runways.geojson").read_text())["features"]:
        props = feature["properties"]
        geom = shape(feature["geometry"])
        if props.get("use") != "PU" or geom.geom_type != "LineString" or not area.intersects(geom):
            continue
        features.append({"type": "Feature", "properties": {"layer": "runway", "id": props.get("id")}, "geometry": mapping(geom)})
    for feature in json.loads((layers_dir / "heliports.geojson").read_text())["features"]:
        geom = shape(feature["geometry"])
        if not area.covers(geom):
            continue
        props = feature["properties"]
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "layer": "heliport",
                    "medical": bool(props.get("medical")),
                    "public": props.get("use") == "PU",
                    "id": props.get("id"),
                    "name": nice_name(props.get("name")),
                },
                "geometry": mapping(geom),
            }
        )
    out.write_text(json.dumps(feature_collection(features), separators=(",", ":")))


def replay_tracks(df: pl.DataFrame, day: str) -> list[dict]:
    """Low tracks for one local day, cut into unbroken segments.

    A track is split wherever the aircraft was not heard for a minute or
    climbed out of the band, so a line on the map never bridges a gap.
    """
    midnight = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp()
    frame = df.filter(
        (pl.col("local_date") == day)
        & pl.col("in_area")
        & ~pl.col("on_ground")
        & pl.col("ac_class").is_not_null()
        & pl.col("agl").is_not_null()
        & (pl.col("agl") >= 0)
        & (pl.col("agl") < REPLAY_TOP_FT)
        & ((pl.col("db_flags") & PRIVACY_FLAGS) == 0)
    ).sort(["icao", "ts"])
    class_code = {name: i for i, name in enumerate(CLASSES)}
    tracks: list[dict] = []
    current = None
    points: list[float] = []
    last_kept = None
    last_seen = None
    cls = 0

    def flush() -> None:
        if len(points) >= 8:
            tracks.append({"c": cls, "p": points.copy()})

    for icao, ts, lat, lon, agl, name in frame.select(["icao", "ts", "lat", "lon", "agl", "ac_class"]).iter_rows():
        if icao != current or (last_seen is not None and ts - last_seen > REPLAY_BREAK_S):
            flush()
            current = icao
            points = []
            last_kept = None
            cls = class_code.get(name, len(CLASSES) - 1)
        last_seen = ts
        if last_kept is not None and ts - last_kept < REPLAY_STEP_S:
            continue
        points.extend((int(round(ts - midnight)), round(lon, 4), round(lat, 4), int(round(agl))))
        last_kept = ts
    flush()
    return tracks


def write_replay(df: pl.DataFrame, out: Path, day: str = REPLAY_DATE) -> dict:
    tracks = replay_tracks(df, day)
    out.write_text(json.dumps({"date": day, "classes": CLASSES, "tracks": tracks}, separators=(",", ":")))
    return {"date": day, "tracks": len(tracks), "points": sum(len(t["p"]) // 4 for t in tracks)}


def write_findings(payload: dict, out: Path) -> None:
    out.write_text(json.dumps(payload, indent=1))


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def summary_lines(findings: dict) -> list[str]:
    """The headline numbers as plain sentences, for the README."""
    study = findings["study"]
    q1, q2, q3, q4, q5 = (findings[key] for key in ("q1", "q2", "q3", "q4", "q5"))
    apt = q2["airports"]
    area = study["area"]
    if "boundary" in study:
        boundary = study["boundary"]
        area_line = (
            f"Proposed operating area: traced from the map in the draft assessment, which gives no coordinates. "
            f"{area['computed_sq_mi']:.0f} sq mi computed, {area['stated_sq_mi']:.0f} stated; "
            f"map fitted to {boundary['dots_matched']} airports and heliports, {boundary['residual_rms_m']:.0f} m root mean square."
        )
    else:
        area_line = f"Operating area: {area['computed_sq_mi']:.0f} sq mi computed, {area['stated_sq_mi']:.0f} stated in the assessment."
    lines = [
        f"Window: {study['window'][0]} to {study['window'][1]} (UTC archive days), "
        f"{findings['window']['hours']} hours.",
        area_line,
        f"Broadcasting is required below 1,200 ft over {_pct(q1['area']['mandatory_share'])} of the area.",
        f"The feed heard at least three aircraft below 580 ft in {_pct(q2['heard_cruise_share'])} of map cells, "
        f"and below 1,200 ft in {_pct(q2['heard_low_share'])}.",
        f"At {apt.get('tracked_to_ground', 0)} of {apt.get('checked', 0)} airports with enough traffic to check, "
        f"most arrivals and departures were heard to within {apt.get('ground_ft', 100):.0f} ft of the ground.",
        f"On average {q3['cruise']['average_present']:.1f} crewed aircraft were in the 80–580 ft band "
        f"and {q3['low']['average_present']:.1f} below 1,200 ft, over the whole area.",
        f"{_pct(q4['public_runway_3nm']['share_of_low'])} of that low time was within 3 NM of a public-use runway.",
        f"A receiver on 1090 MHz alone would miss at least {_pct(q5['single_band_miss_share'])} of observed low time; "
        f"one on both bands at least {_pct(q5['dual_band_miss_share'])}.",
    ]
    return lines


def update_readme(readme: Path, findings: dict, marker: str = "findings") -> None:
    """Rewrite the block between <!-- {marker}:start --> and <!-- {marker}:end -->."""
    if not readme.exists():
        return
    text = readme.read_text()
    start, end = f"<!-- {marker}:start -->", f"<!-- {marker}:end -->"
    if start not in text or end not in text:
        return
    block = start + "\n" + "\n".join(f"- {line}" for line in summary_lines(findings)) + "\n" + end
    pre, rest = text.split(start, 1)
    _, post = rest.split(end, 1)
    readme.write_text(pre + block + post)
