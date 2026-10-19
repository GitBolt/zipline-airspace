"""Answers the five questions from prepared points."""

from __future__ import annotations

import h3
import polars as pl

from survey.constants import (
    CRUISE_HI_FT,
    CRUISE_LO_FT,
    H3_RES,
    LOW_HI_FT,
    LOW_LO_FT,
    MIN_DISTINCT,
    SENSITIVITY_FT,
    SHARE_FLIP_PP,
)
from survey.geometry import area_sq_mi
from survey.hotspots import find_hotspots
from survey.names import nice_name
from survey.reception import airport_reception, cell_reception
from survey.window import feed_health, window_hours
from survey.zones import area_report

CLASSES = ["rotorcraft", "light", "small", "large", "unknown", "other"]
LINKS = ["adsb_1090", "uat_or_adsr", "tisb_only", "mlat_only"]
MISS = ["tisb_only", "mlat_only"]


def _hours(seconds: float) -> float:
    return seconds / 3600.0


def _sum(df: pl.DataFrame) -> float:
    return float(df["dt"].sum()) if not df.is_empty() else 0.0


def _share(part: float, whole: float) -> float | None:
    if whole <= 0:
        return None
    return part / whole


def _band(df: pl.DataFrame, lo: float, hi: float) -> pl.DataFrame:
    return df.filter(
        pl.col("in_area")
        & ~pl.col("on_ground")
        & pl.col("ac_class").is_not_null()
        & pl.col("agl").is_not_null()
        & (pl.col("agl") >= lo)
        & (pl.col("agl") < hi)
        & (pl.col("dt") > 0)
    )


def _by(df: pl.DataFrame, column: str, keys: list) -> dict:
    out = {key: 0.0 for key in keys}
    if df.is_empty():
        return out
    for key, seconds in df.group_by(column).agg(pl.col("dt").sum()).iter_rows():
        if key in out:
            out[key] = _hours(seconds)
    return out


def _by_hour_seconds(df: pl.DataFrame) -> list[float]:
    seconds = [0.0] * 24
    if df.is_empty():
        return seconds
    for hour, value in df.group_by("hour").agg(pl.col("dt").sum()).iter_rows():
        if hour is not None and 0 <= int(hour) <= 23:
            seconds[int(hour)] = float(value)
    return seconds


def _band_summary(df: pl.DataFrame, window: dict) -> dict:
    """Totals, and the same thing as the average number of aircraft in the band.

    Aircraft-seconds divided by the seconds in the window is how many aircraft
    were in the band at a typical moment, which reads more easily than a total
    and does not grow with the length of the window.
    """
    seconds = _sum(df)
    weekday = _sum(df.filter(pl.col("weekday")))
    weekend = seconds - weekday
    by_hour = _by_hour_seconds(df)
    return {
        "aircraft_hours": _hours(seconds),
        "distinct_aircraft": int(df["icao"].n_unique()) if not df.is_empty() else 0,
        "average_present": seconds / (window["hours"] * 3600.0),
        "hours_per_day": _hours(seconds) / (window["hours"] / 24.0),
        "by_class": _by(df, "ac_class", CLASSES),
        "by_hour_present": [
            (value / (count * 3600.0)) if count else 0.0 for value, count in zip(by_hour, window["by_hour"])
        ],
        "weekday_present": weekday / (window["weekday_hours"] * 3600.0) if window["weekday_hours"] else None,
        "weekend_present": weekend / (window["weekend_hours"] * 3600.0) if window["weekend_hours"] else None,
    }


def _q4(low: pl.DataFrame) -> dict:
    if low.is_empty():
        return {}
    total = _sum(low)
    public = low.filter(pl.col("in_public_runway"))
    rest = low.filter(~pl.col("in_public_runway"))
    rest_s = _sum(rest)
    private = rest.filter(pl.col("in_private_runway"))
    after_private = rest.filter(~pl.col("in_private_runway"))
    hospital = after_private.filter(pl.col("in_hospital"))
    after_hospital = after_private.filter(~pl.col("in_hospital"))
    other_h = after_hospital.filter(pl.col("in_other_heliport"))
    elsewhere = after_hospital.filter(~pl.col("in_other_heliport"))

    def pack(frame: pl.DataFrame) -> dict:
        sec = _sum(frame)
        return {
            "hours": _hours(sec),
            "share_of_low": _share(sec, total),
            "share_of_remainder": _share(sec, rest_s),
            "by_class": _by(frame, "ac_class", CLASSES),
        }

    return {
        "low_band_hours": _hours(total),
        "public_runway_3nm": pack(public),
        "remainder_hours": _hours(rest_s),
        "remainder_by_class": _by(rest, "ac_class", CLASSES),
        "private_runway_3nm": pack(private),
        "hospital_1nm": pack(hospital),
        "other_heliport_1nm": pack(other_h),
        "elsewhere": pack(elsewhere),
        "class_d_share_of_remainder": _share(_sum(rest.filter(pl.col("in_class_d"))), rest_s),
    }


def _q5(low: pl.DataFrame) -> dict:
    """Which radio link each aircraft was on, by share of low-band time.

    A receiver that only listens on 1090 MHz misses UAT aircraft as well as the
    ones with no broadcast at all. One that listens on both bands misses only
    the second group. Both shares are lower bounds.
    """
    if low.is_empty():
        return {"low_band_hours": 0.0, "by_link": {}}
    counted = low.filter(pl.col("link").is_in(LINKS))
    seconds = _sum(counted)
    by_link = {}
    for link, hours in _by(counted, "link", LINKS).items():
        by_link[link] = {"hours": hours, "share": _share(hours * 3600.0, seconds)}
    miss_s = _sum(counted.filter(pl.col("link").is_in(MISS)))
    uat_s = _sum(counted.filter(pl.col("link") == "uat_or_adsr"))
    by_class = {}
    for name in CLASSES:
        part = counted.filter(pl.col("ac_class") == name)
        sec = _sum(part)
        if sec <= 0:
            continue
        by_class[name] = {
            "hours": _hours(sec),
            **{link: _share(_sum(part.filter(pl.col("link") == link)), sec) for link in LINKS},
        }

    def side(frame: pl.DataFrame) -> dict:
        sec = _sum(frame)
        return {
            "hours": _hours(sec),
            "dual_band_miss_share": _share(_sum(frame.filter(pl.col("link").is_in(MISS))), sec),
            "uat_share": _share(_sum(frame.filter(pl.col("link") == "uat_or_adsr")), sec),
        }

    aircraft = {
        link: int(n)
        for link, n in counted.group_by("link").agg(pl.col("icao").n_unique()).iter_rows()
    }
    return {
        "low_band_hours": _hours(seconds),
        "by_link": by_link,
        "aircraft_by_link": aircraft,
        "by_class": by_class,
        "dual_band_miss_share": _share(miss_s, seconds),
        "single_band_miss_share": _share(miss_s + uat_s, seconds),
        "uat_share": _share(uat_s, seconds),
        "inside_rule": side(counted.filter(pl.col("in_mandatory"))),
        "outside_rule": side(counted.filter(~pl.col("in_mandatory"))),
        "other_hours": _hours(_sum(low.filter(~pl.col("link").is_in(LINKS)))),
        "lower_bound": True,
    }


def _sensitivity(df: pl.DataFrame) -> dict:
    runs = {}
    for name, delta in (("minus", -SENSITIVITY_FT), ("nominal", 0.0), ("plus", SENSITIVITY_FT)):
        cruise = _band(df, CRUISE_LO_FT + delta, CRUISE_HI_FT + delta)
        low = _band(df, max(0.0, LOW_LO_FT + delta), LOW_HI_FT + delta)
        q5 = _q5(low)
        runs[name] = {
            "cruise_hours": _hours(_sum(cruise)),
            "low_hours": _hours(_sum(low)),
            "public_runway_share": _q4(low).get("public_runway_3nm", {}).get("share_of_low"),
            "single_band_miss_share": q5.get("single_band_miss_share"),
            "dual_band_miss_share": q5.get("dual_band_miss_share"),
            "rotorcraft_share_of_cruise": _share(_sum(cruise.filter(pl.col("ac_class") == "rotorcraft")), _sum(cruise)),
        }
    inconclusive = []
    for key in ("public_runway_share", "single_band_miss_share", "dual_band_miss_share", "rotorcraft_share_of_cruise"):
        base = runs["nominal"][key]
        if base is None:
            continue
        if any(runs[side][key] is not None and abs(runs[side][key] - base) > SHARE_FLIP_PP for side in ("minus", "plus")):
            inconclusive.append(key)
    return {"shift_ft": SENSITIVITY_FT, "flip_pp": SHARE_FLIP_PP, "runs": runs, "inconclusive": inconclusive}


def _sparse(frame: pl.DataFrame, keys: list[str], encode) -> dict[str, list[int]]:
    """Per cell, a flat [code, seconds, code, seconds, ...] list of non-zero bins."""
    out: dict[str, list[int]] = {}
    if frame.is_empty():
        return out
    for row in frame.group_by(["h3", *keys]).agg(pl.col("dt").sum()).iter_rows():
        seconds = int(round(row[-1]))
        if seconds <= 0:
            continue
        code = encode(*row[1:-1])
        if code is None:
            continue
        out.setdefault(row[0], []).extend((code, seconds))
    return out


def _cells(low: pl.DataFrame, cruise: pl.DataFrame, records: dict, masks: dict) -> list[dict]:
    from shapely import contains, points

    class_code = {name: i for i, name in enumerate(CLASSES)}
    link_code = {name: i for i, name in enumerate(LINKS)}

    def by_class_hour(cls, hour):
        if cls not in class_code or hour is None:
            return None
        return class_code[cls] * 24 + int(hour)

    low_x = _sparse(low, ["ac_class", "hour"], by_class_hour)
    cruise_x = _sparse(cruise, ["ac_class", "hour"], by_class_hour)
    links = _sparse(low, ["link"], lambda link: link_code.get(link))
    binned = low.with_columns((pl.col("agl") / 100).floor().cast(pl.Int16).alias("bin"))
    profile = _sparse(binned, ["bin"], lambda b: int(b) if 0 <= int(b) < 12 else None)
    counts = {}
    for frame, key in ((low, "low_n"), (cruise, "cruise_n")):
        if frame.is_empty():
            continue
        for cell, n in frame.group_by("h3").agg(pl.col("icao").n_unique()).iter_rows():
            counts.setdefault(cell, {})[key] = int(n)
    stand_off = {}
    if not low.is_empty():
        for cell, seconds in low.filter(pl.col("in_public_runway")).group_by("h3").agg(pl.col("dt").sum()).iter_rows():
            stand_off[cell] = int(round(seconds))
    ids = list(records)
    centres = [h3.cell_to_latlng(cell) for cell in ids]
    pts = points([c[1] for c in centres], [c[0] for c in centres])
    in_rule = contains(masks["mandatory"], pts)
    in_buffer = contains(masks["public_runway"], pts)
    tier_code = {"cruise": 0, "low": 1, "higher": 2, "none": 3}
    cells = []
    for i, cell in enumerate(ids):
        rec = records[cell]
        item = {
            "h": cell,
            "lat": round(centres[i][0], 4),
            "lon": round(centres[i][1], 4),
            "t": tier_code[rec["tier"]],
            "v": int(bool(in_rule[i])),
            "b": int(bool(in_buffer[i])),
        }
        if rec["lowest_ft"] is not None:
            item["lh"] = int(round(rec["lowest_ft"]))
        n = counts.get(cell, {})
        if n.get("low_n"):
            item["ln"] = n["low_n"]
        if n.get("cruise_n"):
            item["cn"] = n["cruise_n"]
        for key, source in (("lx", low_x), ("cx", cruise_x), ("k", links), ("p", profile)):
            if cell in source:
                item[key] = source[cell]
        if cell in stand_off:
            item["so"] = stand_off[cell]
        cells.append(item)
    return cells


def compute(df: pl.DataFrame, areas: dict, dates: list[str], facilities: dict, hub: tuple[float, float]) -> tuple[dict, list[dict]]:
    """`facilities` holds `airports` and `heliports` GeoJSON features and `runway_ends`.

    `hub` is the (lon, lat) of the airport that reception distances are measured from.
    """
    masks = areas["masks"]
    window = window_hours(dates)
    q1_area = area_report(masks)
    ring = [(lat, lon) for lon, lat in masks["area"].exterior.coords]
    cell_ids = set(h3.polygon_to_cells(h3.LatLngPoly(ring[:-1]), H3_RES))
    cell_ids |= set(df.filter(pl.col("in_area") & (pl.col("h3") != ""))["h3"].unique().to_list())
    cell_ids = sorted(cell_ids)

    low = _band(df, LOW_LO_FT, LOW_HI_FT)
    cruise = _band(df, CRUISE_LO_FT, CRUISE_HI_FT)
    low_s = _sum(low)
    mandatory_s = _sum(low.filter(pl.col("in_mandatory")))
    q1 = {
        "area": q1_area,
        "traffic": {
            "low_band_hours": _hours(low_s),
            "share_where_required": _share(mandatory_s, low_s),
            "share_outside_rule": _share(low_s - mandatory_s, low_s),
        },
    }
    q2, records = cell_reception(df, cell_ids)
    in_area = [f for f in facilities["airports"] if masks["area"].covers(_point(f))]
    q2["airports"] = airport_reception(df, in_area, facilities["runway_ends"], hub)
    q3 = {
        "cruise": _band_summary(cruise, window),
        "low": _band_summary(low, window),
        "min_distinct_for_a_cell": MIN_DISTINCT,
    }
    q4 = _q4(low)
    q4["medical_heliports"] = q1_area["medical_heliports"]
    q4["heliports"] = q1_area["heliports"]
    public = [_place(f) for f in in_area if f["properties"]["use"] == "PU"]
    pads = [_place(f) for f in in_area if f["properties"]["use"] != "PU"]
    pads += [_place(f) for f in facilities["heliports"] if masks["area"].covers(_point(f))]
    q4["hotspots"] = find_hotspots(cruise.filter(~pl.col("in_public_runway")), window["hours"], public, pads)
    q5 = _q5(low)
    non_crewed = df.filter(pl.col("in_area") & pl.col("ac_class").is_null() & ~pl.col("on_ground") & (pl.col("dt") > 0))
    findings = {
        "window": window,
        "health": feed_health(df, dates),
        "q1": q1,
        "q2": q2,
        "q3": q3,
        "q4": q4,
        "q5": q5,
        "checks": {
            "low_class_hours": sum(q3["low"]["by_class"].values()),
            "low_hours": q3["low"]["aircraft_hours"],
            "low_zone_hours": q4["public_runway_3nm"]["hours"] + q4["remainder_hours"] if q4 else 0.0,
        },
        "sensitivity": _sensitivity(df),
        "excluded_non_crewed_hours": _hours(_sum(non_crewed)),
        "area_sq_mi_projected": area_sq_mi(masks["area"]),
    }
    return findings, _cells(low, cruise, records, masks)


def _point(feature: dict):
    from shapely.geometry import shape

    return shape(feature["geometry"])


def _place(feature: dict) -> dict:
    lon, lat = feature["geometry"]["coordinates"]
    props = feature["properties"]
    return {"id": props["id"], "name": nice_name(props["name"]), "lon": lon, "lat": lat}
