"""Attach height, class, link, zones, and time intervals to raw points."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import h3
import numpy as np
import polars as pl
from shapely import STRtree, distance, points
from survey.altitude import (
    agl_from_barometric,
    agl_from_geometric,
    choose_basis,
    load_geoid,
    residual_histogram,
    sample_geoid,
    summarize_residuals,
)
from survey.classify import aircraft_class, link_class
from survey.constants import (
    BASIS_MARGIN_FT,
    GAP_BREAK_S,
    GAP_CAP_S,
    H3_RES,
    KEEP_BELOW_FT,
    MIN_CAL_POINTS,
    TIMEZONE,
)
from survey.geometry import TO_ALBERS
from survey.terrain import Terrain
from survey.zones import build_masks, mask

FT_PER_M = 3.280839895


def load_raw(raw_dir: Path, dates: list[str]) -> pl.DataFrame:
    frames = []
    for date in dates:
        for kind in ("prod", "mlatonly"):
            path = raw_dir / f"{date}-{kind}.parquet"
            if path.exists():
                frames.append(pl.read_parquet(path))
    if not frames:
        raise FileNotFoundError(f"no raw parquet in {raw_dir}")
    df = pl.concat(frames, how="vertical")
    df = df.with_columns(
        pl.col("ts").round(0).alias("ts_r"),
        pl.when(pl.col("feed") == "prod").then(0).otherwise(1).alias("frank"),
    )
    return (
        df.sort(["icao", "ts_r", "frank"])
        .unique(subset=["icao", "ts_r"], keep="first", maintain_order=True)
        .drop(["ts_r", "frank"])
    )


def _parse_valid(text: str) -> int | None:
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def altimeter_array(lat: np.ndarray, lon: np.ndarray, ts: np.ndarray, metars: list[dict]) -> np.ndarray:
    by_hour: dict[int, np.ndarray] = {}
    for row in metars:
        epoch = _parse_valid(str(row["valid"]))
        if epoch is None:
            continue
        by_hour.setdefault(epoch // 3600, []).append((row["lat"], row["lon"], row["alti"]))
    packed = {hour: np.array(rows, dtype=np.float64) for hour, rows in by_hour.items() if rows}
    out = np.full(len(ts), np.nan)
    hours = (ts // 3600).astype(np.int64)
    for hour in np.unique(hours):
        stations = packed.get(int(hour))
        if stations is None:
            stations = packed.get(int(hour) - 1)
        if stations is None:
            stations = packed.get(int(hour) + 1)
        if stations is None:
            continue
        sel = np.flatnonzero(hours == hour)
        dlat = lat[sel, None] - stations[None, :, 0]
        dlon = lon[sel, None] - stations[None, :, 1]
        nearest = np.argmin(dlat * dlat + dlon * dlon, axis=1)
        out[sel] = stations[nearest, 2]
    return out


def _calibration(lat, lon, geom, on_ground, icao, terrain_m, geoid_n, ends: list[dict]) -> dict:
    if not ends:
        return {"basis": "hae", "global": {}, "per_aircraft_msl": [], "histogram": []}
    end_ll = np.array([[e["lon"], e["lat"]] for e in ends], dtype=np.float64)
    end_xy = np.array(TO_ALBERS.transform(end_ll[:, 0], end_ll[:, 1])).T
    end_pts = points(end_xy[:, 0], end_xy[:, 1])
    tree = STRtree(end_pts)
    elev = np.array([e["elev_ft"] for e in ends], dtype=np.float64)
    ground = np.flatnonzero(on_ground & np.isfinite(geom) & np.isfinite(terrain_m))
    if ground.size == 0:
        return {"basis": "hae", "global": {"n": 0}, "per_aircraft_msl": [], "histogram": []}
    g_ll = np.column_stack([lon[ground], lat[ground]])
    g_xy = np.array(TO_ALBERS.transform(g_ll[:, 0], g_ll[:, 1])).T
    g_pts = points(g_xy[:, 0], g_xy[:, 1])
    nearest = np.asarray(tree.nearest(g_pts))
    dist = np.asarray(distance(g_pts, end_pts[nearest]))
    close = dist <= 200.0
    if not np.any(close):
        return {"basis": "hae", "global": {"n": 0}, "per_aircraft_msl": [], "histogram": []}
    idx = ground[close]
    matched = nearest[close]
    terrain_ft = terrain_m[idx] * FT_PER_M
    residual_hae = agl_from_geometric(geom[idx], terrain_m[idx], geoid_n[idx], as_msl=False)
    # On-runway residual is orthometric height minus published runway elevation,
    # which is the same comparison as AGL against a runway that sits on the terrain model
    # only when the model and the runway agree. Use the published elevation directly.
    ortho_hae = geom[idx] - geoid_n[idx] * FT_PER_M
    ortho_msl = geom[idx]
    residual_hae = ortho_hae - elev[matched]
    residual_msl = ortho_msl - elev[matched]
    _ = terrain_ft
    basis = choose_basis(residual_hae, residual_msl)
    msl_aircraft = []
    icaos = icao[idx]
    for ident in np.unique(icaos):
        sel = icaos == ident
        if int(sel.sum()) < MIN_CAL_POINTS:
            continue
        if choose_basis(residual_hae[sel], residual_msl[sel]) == "msl" and basis != "msl":
            hae_mae = summarize_residuals(residual_hae[sel])["mae_ft"] or 0
            msl_mae = summarize_residuals(residual_msl[sel])["mae_ft"] or 0
            if msl_mae + BASIS_MARGIN_FT < hae_mae:
                msl_aircraft.append(str(ident))
    chosen = residual_msl if basis == "msl" else residual_hae
    return {
        "basis": basis,
        "global_hae": summarize_residuals(residual_hae),
        "global_msl": summarize_residuals(residual_msl),
        "chosen": summarize_residuals(chosen),
        "per_aircraft_msl_n": len(msl_aircraft),
        "msl_aircraft": set(msl_aircraft),
        "histogram": residual_histogram(chosen),
        "runway_matches": int(close.sum()),
    }


def prepare(df: pl.DataFrame, layers: Path, terrain: Terrain, area) -> tuple[pl.DataFrame, dict, dict]:
    """Height on every point, then zones and time only on the points that matter.

    Height, surveillance link and the gap to the next report use the whole
    track. Zone tagging is the slow part, so it runs after the frame is cut to
    ground reports and airborne points below KEEP_BELOW_FT.
    """
    metars = json.loads((layers / "metar.json").read_text())
    ends = json.loads((layers / "runway_ends.json").read_text())
    geoid_doc = load_geoid(layers / "geoid18.json")
    df = df.sort(["icao", "ts"])
    lon = df["lon"].to_numpy()
    lat = df["lat"].to_numpy()
    geom = df["alt_geom"].to_numpy().astype(np.float64)
    baro = df["alt_baro"].to_numpy().astype(np.float64)
    ts = df["ts"].to_numpy()
    on_ground = df["on_ground"].to_numpy()
    icao = df["icao"].to_numpy()
    terrain_m = terrain.sample_m(lon, lat)
    geoid_n = sample_geoid(geoid_doc, lat, lon)
    alti = altimeter_array(lat, lon, ts, metars)
    calibration = _calibration(lat, lon, geom, on_ground, icao, terrain_m, geoid_n, ends)
    msl_set = calibration.pop("msl_aircraft", set())
    if calibration["basis"] == "msl":
        use_msl = np.ones(len(df), dtype=bool)
    elif msl_set:
        use_msl = df["icao"].is_in(list(msl_set)).to_numpy()
    else:
        use_msl = np.zeros(len(df), dtype=bool)
    agl = np.full(len(df), np.nan)
    has_geom = np.isfinite(geom) & np.isfinite(terrain_m) & np.isfinite(geoid_n)
    sel = has_geom & ~use_msl
    agl[sel] = agl_from_geometric(geom[sel], terrain_m[sel], geoid_n[sel], False)
    sel = has_geom & use_msl
    agl[sel] = agl_from_geometric(geom[sel], terrain_m[sel], geoid_n[sel], True)
    need_baro = ~np.isfinite(agl) & np.isfinite(baro) & np.isfinite(alti) & np.isfinite(terrain_m)
    agl[need_baro] = agl_from_barometric(baro[need_baro], terrain_m[need_baro], alti[need_baro])
    basis_name = np.where(has_geom, np.where(use_msl, "geometric_msl", "geometric_hae"), "barometric")
    basis_name = np.where(np.isfinite(agl), basis_name, "none")
    airborne = ~on_ground
    calibration["geoid_model"] = geoid_doc.get("model")
    calibration["metar_reports"] = len(metars)
    calibration["points"] = int(len(df))
    calibration["airborne_points"] = int(airborne.sum())
    for key, name in (
        ("geometric_hae", "agl_geometric_share"),
        ("geometric_msl", "agl_geometric_msl_share"),
        ("barometric", "agl_barometric_share"),
        ("none", "agl_missing_share"),
    ):
        calibration[name] = float(np.mean(basis_name[airborne] == key)) if airborne.any() else 0.0

    print("links")
    link_rows = df.group_by(["date", "icao"]).agg(pl.col("source").unique())
    link_frame = pl.DataFrame(
        {
            "date": link_rows["date"],
            "icao": link_rows["icao"],
            "link": [link_class(set(sources)) for sources in link_rows["source"].to_list()],
        }
    )
    df = df.with_columns(
        pl.Series("agl", agl).fill_nan(None),
        pl.Series("alt_basis", basis_name),
        (pl.col("ts").shift(-1).over("icao") - pl.col("ts")).alias("gap"),
        (pl.col("ts") - pl.col("ts").shift(1).over("icao")).alias("prev_gap"),
    ).join(link_frame, on=["date", "icao"], how="left", maintain_order="left")
    total_points = df.height
    df = df.filter(pl.col("on_ground") | (pl.col("agl").is_not_null() & (pl.col("agl") < KEEP_BELOW_FT)))
    calibration["kept_points"] = int(df.height)
    print(f"kept {df.height} of {total_points} points below {KEEP_BELOW_FT:.0f} ft or on the ground")
    lon = df["lon"].to_numpy()
    lat = df["lat"].to_numpy()

    masks = build_masks(layers, terrain, area)
    print("tagging zones")
    flags = {
        "in_area": mask(masks["area"], lon, lat),
        "in_veil": mask(masks["veil"], lon, lat),
        "in_mandatory": mask(masks["mandatory"], lon, lat),
        "in_public_runway": mask(masks["public_runway"], lon, lat),
        "in_private_runway": mask(masks["private_runway"], lon, lat),
        "in_hospital": mask(masks["hospital"], lon, lat),
        "in_other_heliport": mask(masks["other_heliport"], lon, lat),
        "in_class_d": mask(masks["class_d_surface"], lon, lat),
    }
    print("h3")
    cells = [
        h3.latlng_to_cell(float(a), float(b), H3_RES) if inside else ""
        for a, b, inside in zip(lat, lon, flags["in_area"])
    ]
    pairs: dict[tuple, str | None] = {}
    classes = []
    for key in zip(df["category"].to_list(), df["type_code"].to_list()):
        if key not in pairs:
            pairs[key] = aircraft_class(*key)
        classes.append(pairs[key])
    local = (
        pl.from_epoch(df["ts"].cast(pl.Int64), time_unit="s")
        .dt.replace_time_zone("UTC")
        .dt.convert_time_zone(TIMEZONE)
    )
    out = df.with_columns(
        [
            pl.Series("h3", cells),
            pl.Series("ac_class", classes, dtype=pl.String),
            local.dt.hour().cast(pl.Int8).alias("hour"),
            (local.dt.weekday() <= 5).alias("weekday"),
            local.dt.strftime("%Y-%m-%d").alias("local_date"),
            *[pl.Series(name, values) for name, values in flags.items()],
        ]
    )
    out = out.with_columns(
        pl.when((pl.col("gap") > 0) & (pl.col("gap") < GAP_BREAK_S) & ~pl.col("on_ground"))
        .then(pl.min_horizontal(pl.col("gap"), pl.lit(GAP_CAP_S)))
        .otherwise(0.0)
        .alias("dt")
    )
    return out, calibration, {"masks": masks}
