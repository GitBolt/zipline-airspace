"""Zone masks: veil, runway stand-off, heliports, classes."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import contains, points
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from survey.constants import HELIPORT_BUFFER_NM, LOW_HI_FT, RUNWAY_BUFFER_NM
from survey.geometry import area_sq_mi, buffer_nm, union_all

FT_PER_M = 3.280839895


def _features(path: Path) -> list[dict]:
    return json.loads(path.read_text())["features"]


def _floor_agl(props: dict, geom, terrain) -> float:
    code = str(props.get("LOWER_CODE") or "").upper()
    raw = props.get("LOWER_VAL")
    try:
        val = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        val = 0.0
    if code in {"", "SFC"} or val <= 0:
        return 0.0
    if code == "AGL":
        return val
    centroid = geom.centroid
    sampled = terrain.sample_m(np.array([centroid.x]), np.array([centroid.y]))[0]
    terrain_ft = float(sampled) * FT_PER_M if np.isfinite(sampled) else 650.0
    return val - terrain_ft


def _collect(features: list[dict], predicate) -> list:
    geoms = []
    for feature in features:
        props = feature.get("properties") or {}
        if str(props.get("EXCLUSION") or "0") in {"1", "True"}:
            continue
        geom = shape(feature["geometry"])
        if geom.is_empty:
            continue
        if predicate(props, geom):
            geoms.append(geom)
    return geoms


def build_masks(layers: Path, terrain, area: Polygon) -> dict:
    """Every zone, clipped to `area`, the metro's operating-area polygon."""
    airspace = _features(layers / "class_airspace.geojson")
    runways = _features(layers / "runways.geojson")
    heliports = _features(layers / "heliports.geojson")

    def low_enough(props, geom, classes: set[str]) -> bool:
        if str(props.get("CLASS") or "").upper() not in classes:
            return False
        if str(props.get("TYPE_CODE") or "").upper() not in {"CLASS", ""}:
            return False
        return _floor_agl(props, geom, terrain) <= LOW_HI_FT

    veil_geoms = _collect(
        airspace,
        lambda props, _geom: str(props.get("TYPE_CODE") or "").upper() == "MODE-C",
    )
    class_b_low = _collect(airspace, lambda props, geom: low_enough(props, geom, {"B"}))
    class_c_low = _collect(airspace, lambda props, geom: low_enough(props, geom, {"C"}))
    class_d_surface = _collect(
        airspace,
        lambda props, geom: str(props.get("CLASS") or "").upper() == "D" and _floor_agl(props, geom, terrain) <= 0.0,
    )
    # Surface Class D has floor 0, so <= 0 catches SFC. Shelves above the surface do not.
    class_b_surface = _collect(
        airspace,
        lambda props, geom: str(props.get("CLASS") or "").upper() == "B" and _floor_agl(props, geom, terrain) <= 0.0,
    )
    class_c_surface = _collect(
        airspace,
        lambda props, geom: str(props.get("CLASS") or "").upper() == "C" and _floor_agl(props, geom, terrain) <= 0.0,
    )

    airports = _features(layers / "airports.geojson")

    def runway_union(use: str):
        """3 NM around every runway of that use.

        Some small airports have no surveyed runway ends in NASR. They are
        buffered from the airport reference point so they are not left out.
        """
        lines = []
        drawn = set()
        for feature in runways:
            props = feature["properties"]
            if props.get("use") != use or props.get("type") not in {"A", "C"}:
                continue
            lines.append(shape(feature["geometry"]))
            drawn.add(props.get("id"))
        for feature in airports:
            props = feature["properties"]
            if props.get("use") == use and props.get("type") != "H" and props.get("id") not in drawn:
                lines.append(shape(feature["geometry"]))
        if not lines:
            return Polygon()
        return buffer_nm(unary_union(lines), RUNWAY_BUFFER_NM)

    def heli_union(medical: bool | None):
        pts = []
        for feature in heliports:
            flag = bool(feature["properties"].get("medical"))
            if medical is not None and flag != medical:
                continue
            pts.append(shape(feature["geometry"]))
        if not pts:
            return Polygon()
        return buffer_nm(unary_union(pts), HELIPORT_BUFFER_NM)

    veil = union_all(veil_geoms)
    b_low = union_all(class_b_low)
    c_low = union_all(class_c_low)
    mandatory = union_all([veil, b_low, c_low]).intersection(area)
    carveout = veil.intersection(area).difference(union_all([b_low, c_low]))
    return {
        "area": area,
        "veil": veil.intersection(area),
        "class_b_low": b_low.intersection(area),
        "class_c_low": c_low.intersection(area),
        "class_b_surface": union_all(class_b_surface).intersection(area),
        "class_c_surface": union_all(class_c_surface).intersection(area),
        "class_d_surface": union_all(class_d_surface).intersection(area),
        "mandatory": mandatory,
        "carveout": carveout if not carveout.is_empty else veil.intersection(area).difference(b_low),
        "public_runway": runway_union("PU").intersection(area.buffer(0.05)),
        "private_runway": runway_union("PR").intersection(area.buffer(0.05)),
        "hospital": heli_union(True).intersection(area.buffer(0.02)),
        "other_heliport": heli_union(False).intersection(area.buffer(0.02)),
        "medical_count": sum(
            1
            for feature in heliports
            if feature["properties"].get("medical") and area.covers(shape(feature["geometry"]))
        ),
        "heliport_count": sum(1 for feature in heliports if area.covers(shape(feature["geometry"]))),
        "public_airport_count": sum(
            1
            for feature in airports
            if feature["properties"].get("use") == "PU" and area.covers(shape(feature["geometry"]))
        ),
    }


def mask(geom, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    if geom is None or geom.is_empty:
        return np.zeros(lon.shape, dtype=bool)
    return np.asarray(contains(geom, points(lon, lat)), dtype=bool)


def area_report(masks: dict) -> dict:
    area = area_sq_mi(masks["area"])
    mandatory = area_sq_mi(masks["mandatory"])
    carveout = area_sq_mi(masks["carveout"])
    outside = max(area - mandatory, 0.0)
    return {
        "operating_sq_mi": area,
        "mandatory_sq_mi": mandatory,
        "mandatory_share": mandatory / area if area else None,
        "carveout_sq_mi": carveout,
        "carveout_share": carveout / area if area else None,
        "outside_rule_sq_mi": outside,
        "outside_rule_share": outside / area if area else None,
        "veil_sq_mi": area_sq_mi(masks["veil"]),
        "class_b_low_sq_mi": area_sq_mi(masks["class_b_low"]),
        "class_c_low_sq_mi": area_sq_mi(masks["class_c_low"]),
        "class_d_surface_sq_mi": area_sq_mi(masks["class_d_surface"]),
        "medical_heliports": masks["medical_count"],
        "heliports": masks["heliport_count"],
        "public_airports": masks["public_airport_count"],
    }
