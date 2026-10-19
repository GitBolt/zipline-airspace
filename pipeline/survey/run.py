"""Reproduce the survey for one metro from public sources."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import polars as pl

from survey.constants import (
    CRUISE_FT,
    CRUISE_HI_FT,
    CRUISE_LO_FT,
    ENROUTE_CEILING_FT,
    GAP_CAP_S,
    H3_RES,
    HELIPORT_BUFFER_NM,
    LOW_HI_FT,
    LOW_LO_FT,
    REPLAY_DATE,
    RUNWAY_BUFFER_NM,
    SITE_RADIUS_MI,
    STUDY_DATES,
    TIMEZONE,
)
from survey.export_web import update_readme, write_findings, write_layers, write_replay
from survey.fetch_layers import fetch_all as fetch_layers
from survey.fetch_tracks import extract_dates
from survey.geometry import area_check, operating_polygon
from survey.georef import TRACE_FILE
from survey.metrics import CLASSES, LINKS, compute
from survey.metros import METROS, Metro, get
from survey.prepare import load_raw, prepare
from survey.terrain import Terrain
from survey.zones import build_masks

ROOT = Path(__file__).resolve().parents[2]


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if k != "masks"}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return round(value, 6)
    if hasattr(value, "item"):
        return _clean(value.item())
    return value


def _features(path: Path) -> list[dict]:
    return json.loads(path.read_text())["features"]


def boundary_summary(metro: Metro, trace_file: Path = TRACE_FILE) -> dict:
    """How a traced boundary was made and how far to trust it, from the trace file.

    Stops if the corners in survey.metros no longer match the trace.
    """
    doc = json.loads(trace_file.read_text())
    entry = doc["outlines"][metro.key]
    for got, want in zip(metro.corners, entry["corners_lonlat"], strict=True):
        if max(abs(got[0] - want[0]), abs(got[1] - want[1])) > 1e-6:
            raise SystemExit(f"{metro.key} corners differ from {trace_file.name}; run python -m survey.georef and copy them")
    return {
        "method": "traced",
        "source": doc["source"],
        "note": metro.boundary_note,
        "corners": entry["corners_lonlat"],
        "map_projection": doc["crs_name"],
        "metres_per_pixel": doc["metres_per_px"],
        "dots_found": doc["dots_found"],
        "dots_matched": doc["dots_matched"],
        "residual_rms_m": doc["residual_rms_m"],
        "residual_max_m": doc["residual_max_m"],
        "corner_shift_bootstrap_p95_m": entry["corner_shift_bootstrap_p95_m"],
        "corner_shift_other_projections_m": entry["corner_shift_other_projections_m"],
        "area_if_edges_move_1px_sq_mi": entry["area_if_edges_move_1px_sq_mi"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metro", default="dfw", help=f"one of {', '.join(METROS)}")
    parser.add_argument("--dates", nargs="*", default=STUDY_DATES)
    parser.add_argument("--from-raw", action="store_true", help="skip downloads; use data/raw")
    parser.add_argument("--from-prepared", action="store_true", help="skip height and zones; use data/agl")
    parser.add_argument("--out", type=Path, default=None, help="default: the metro's folder under web/public/data")
    args = parser.parse_args()
    metro = get(args.metro)
    dates = args.dates
    layers = metro.layers_dir
    cache = metro.cache
    cal_cache = cache.with_suffix(".calibration.json")
    area = operating_polygon(metro)
    offline = args.from_raw or args.from_prepared
    if not offline and (not (layers / "class_airspace.geojson").exists() or not (layers / "geoid18.json").exists()):
        fetch_layers(metro)
    if not offline:
        extract_dates(dates, [metro])
    terrain = Terrain(ROOT / "data" / "terrain", metro.dem_tiles)
    try:
        if args.from_prepared and cache.exists():
            prepared = pl.read_parquet(cache)
            calibration = json.loads(cal_cache.read_text())
            areas = {"masks": build_masks(layers, terrain, area)}
        else:
            print("loading")
            frame = load_raw(metro.raw_dir, dates)
            prepared, calibration, areas = prepare(frame, layers, terrain, area)
            calibration = _clean(calibration)
            cache.parent.mkdir(parents=True, exist_ok=True)
            prepared.write_parquet(cache, compression="zstd")
            cal_cache.write_text(json.dumps(calibration))
    finally:
        terrain.close()
    print(f"prepared {prepared.height} points")
    facilities = {
        "airports": _features(layers / "airports.geojson"),
        "heliports": _features(layers / "heliports.geojson"),
        "runway_ends": json.loads((layers / "runway_ends.json").read_text()),
    }
    findings, cells = compute(prepared, areas, dates, facilities, metro.hub)
    findings = _clean(findings)
    replay_day = REPLAY_DATE
    study = {
        "name": metro.survey_name,
        "window": [dates[0], dates[-1]],
        "timezone": TIMEZONE,
        "area": _clean(area_check(areas["masks"]["area"], metro.stated_area_sq_mi)),
    }
    if metro.traced:
        study["boundary"] = _clean(boundary_summary(metro))
    else:
        study["longitude_correction"] = metro.boundary_note
    findings["study"] = {
        **study,
        "instrument": "adsb.lol readsb archives, production feed plus mlat-only feed",
        "replay_date": replay_day,
        "low_band_ft": [LOW_LO_FT, LOW_HI_FT],
        "cruise_band_ft": [CRUISE_LO_FT, CRUISE_HI_FT],
        "cruise_ft": CRUISE_FT,
        "enroute_ceiling_ft": ENROUTE_CEILING_FT,
        "runway_buffer_nm": RUNWAY_BUFFER_NM,
        "heliport_buffer_nm": HELIPORT_BUFFER_NM,
        "site_radius_mi": SITE_RADIUS_MI,
        "gap_cap_s": GAP_CAP_S,
        "h3_resolution": H3_RES,
        "classes": CLASSES,
        "links": LINKS,
    }
    findings["calibration"] = calibration
    out = args.out or metro.web_dir
    out.mkdir(parents=True, exist_ok=True)
    write_findings(findings, out / "findings.json")
    (out / "cells.json").write_text(json.dumps(cells, separators=(",", ":")))
    write_layers(areas["masks"], layers, out / "layers.geojson", findings["q2"]["airports"])
    replay_info = write_replay(prepared, out / "replay.json", replay_day)
    if out == metro.web_dir:
        update_readme(ROOT / "README.md", findings, "findings" if metro.key == "dfw" else f"findings:{metro.key}")
        for name in ("METHODS.md", "LIMITS.md"):
            shutil.copyfile(ROOT / name, ROOT / "web" / "public" / name)
    for key in ("q1", "q3", "q5"):
        print(key, json.dumps({k: v for k, v in findings[key].items() if not isinstance(v, (dict, list))}))
    print("q2", {k: v for k, v in findings["q2"].items() if k != "airports"}, {k: v for k, v in findings["q2"]["airports"].items() if k not in ("table",)})
    print("replay", replay_info)
    print("thin hours", len(findings["health"]["thin_hours"]))


if __name__ == "__main__":
    main()
