"""Tests for the measures added on top of height and zones."""

import json
from pathlib import Path

import h3
import numpy as np
import polars as pl
import pytest

from survey.constants import H3_RES, MIN_SUPPORT, MIN_VISITS
from survey.export_web import replay_tracks
from survey.hotspots import _components, find_hotspots
from survey.metrics import CLASSES, _band, _band_summary, _q5, _sparse
from survey.names import nice_name
from survey.reception import airport_reception, cell_reception, compass
from survey.window import feed_health, window_hours

ROOT = Path(__file__).resolve().parents[2]
DFW = (32.8968, -97.0380)


def points(rows: list[dict]) -> pl.DataFrame:
    """A prepared-points frame with sensible defaults for the columns a test does not care about."""
    base = {
        "date": "2026-09-29",
        "local_date": "2026-09-29",
        "icao": "a00001",
        "ts": 0.0,
        "lat": DFW[0],
        "lon": DFW[1],
        "agl": 300.0,
        "on_ground": False,
        "ac_class": "light",
        "link": "adsb_1090",
        "hour": 12,
        "weekday": True,
        "in_area": True,
        "in_mandatory": True,
        "in_public_runway": False,
        "gap": 5.0,
        "prev_gap": 5.0,
        "dt": 5.0,
        "db_flags": 0,
    }
    filled = [{**base, **row} for row in rows]
    for row in filled:
        row.setdefault("h3", h3.latlng_to_cell(row["lat"], row["lon"], H3_RES))
    schema = {"agl": pl.Float64, "gap": pl.Float64, "prev_gap": pl.Float64, "hour": pl.Int8, "db_flags": pl.Int16}
    return pl.DataFrame(filled, schema_overrides=schema)


def test_window_counts_local_hours_from_utc_days():
    window = window_hours(["2026-09-28", "2026-09-29"])
    assert window["hours"] == 48
    assert window["by_hour"] == [2] * 24
    # UTC midnight on Monday 28 Sep is 19:00 Sunday in Dallas: five weekend hours.
    assert window["weekend_hours"] == 5
    assert window["weekday_hours"] == 43
    assert window["local_start"] == "2026-09-27 19:00"


def test_average_present_is_seconds_over_window_seconds():
    window = window_hours(["2026-09-29"])
    frame = points([{"dt": 3600.0}, {"dt": 3600.0, "icao": "a00002", "ac_class": "rotorcraft"}])
    summary = _band_summary(_band(frame, 80, 580), window)
    assert summary["average_present"] == pytest.approx(2 * 3600 / (24 * 3600))
    assert summary["hours_per_day"] == pytest.approx(2.0)
    assert summary["by_hour_present"][12] == pytest.approx(2.0)
    assert summary["distinct_aircraft"] == 2
    assert summary["by_class"]["rotorcraft"] == pytest.approx(1.0)


def test_band_leaves_out_ground_reports_and_non_crewed():
    frame = points([{"agl": 300.0}, {"agl": 300.0, "on_ground": True}, {"agl": 300.0, "ac_class": None}, {"agl": 900.0}])
    assert _band(frame, 80, 580).height == 1


def test_cell_reception_needs_several_different_aircraft():
    cell = h3.latlng_to_cell(*DFW, H3_RES)
    quiet = h3.latlng_to_cell(32.3, -96.2, H3_RES)
    rows = [{"icao": f"a{i}", "agl": 200.0 + i} for i in range(MIN_SUPPORT)]
    rows += [{"icao": "b1", "agl": 100.0, "lat": 32.3, "lon": -96.2}]
    summary, records = cell_reception(points(rows), [cell, quiet, "empty"])
    assert records[cell]["tier"] == "cruise"
    assert records[cell]["lowest_ft"] == pytest.approx(200.0 + MIN_SUPPORT - 1)
    # One low pass is not enough to say the feed hears low there.
    assert records[quiet]["tier"] == "higher"
    assert records["empty"]["tier"] == "none"
    assert summary["tiers"] == {"cruise": 1, "low": 0, "higher": 1, "none": 1}


def test_cell_reception_low_tier():
    rows = [{"icao": f"a{i}", "agl": 900.0} for i in range(MIN_SUPPORT)]
    frame = points(rows)
    _, records = cell_reception(frame, [frame["h3"][0]])
    assert records[frame["h3"][0]]["tier"] == "low"


def airport(ident="KTST", lat=DFW[0], lon=DFW[1], use="PU"):
    return {
        "type": "Feature",
        "properties": {"id": ident, "name": "TEST MUNI", "use": use, "type": "A"},
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def test_airport_reception_counts_arrivals_not_flybys():
    rows = []
    # Arrivals: the track ends near the runway. Half are lost at 400 ft, half reach the ground.
    for i in range(MIN_VISITS):
        rows.append({"icao": f"lost{i}", "agl": 400.0, "gap": None})
        rows.append({"icao": f"down{i}", "agl": 20.0, "gap": None})
    # A fly-by at 500 ft with an unbroken track is not a visit.
    rows.append({"icao": "flyby", "agl": 500.0, "gap": 5.0, "prev_gap": 5.0})
    # Too high to be landing.
    rows.append({"icao": "high", "agl": 1400.0, "gap": None})
    # Far from the runway.
    rows.append({"icao": "far", "agl": 100.0, "gap": None, "lat": DFW[0] + 0.5})
    out = airport_reception(points(rows), [airport()], [{"id": "KTST", "lon": DFW[1], "lat": DFW[0]}], (DFW[1], DFW[0]))
    assert out["checked"] == 1
    row = out["table"][0]
    assert row["visits"] == 2 * MIN_VISITS
    assert row["to_ground_share"] == pytest.approx(0.5)
    assert row["median_lowest_ft"] == pytest.approx(210.0)
    assert row["name"] == "Test Municipal"
    assert row["hub_nm"] == pytest.approx(0.0, abs=0.01)


def test_airport_reception_uses_reference_point_without_runway_ends():
    rows = [{"icao": f"g{i}", "agl": 0.0, "on_ground": True} for i in range(MIN_VISITS)]
    out = airport_reception(points(rows), [airport()], [], (DFW[1], DFW[0]))
    assert out["checked"] == 1
    assert out["table"][0]["to_ground_share"] == 1.0
    assert out["tracked_to_ground"] == 1


def test_airport_reception_skips_thin_airports():
    rows = [{"icao": f"g{i}", "agl": 0.0, "gap": None} for i in range(MIN_VISITS - 1)]
    assert airport_reception(points(rows), [airport()], [], (DFW[1], DFW[0]))["checked"] == 0


def test_components_join_neighbours_only():
    a = h3.latlng_to_cell(*DFW, H3_RES)
    b = sorted(h3.grid_ring(a, 1))[0]
    far = h3.latlng_to_cell(33.4, -96.0, H3_RES)
    groups = _components({a, b, far})
    assert sorted(len(g) for g in groups) == [1, 2]


def test_hotspot_is_described_relative_to_the_nearest_public_airport():
    rows = [{"icao": f"h{i}", "dt": 400.0, "ac_class": "rotorcraft", "hour": 8} for i in range(3)]
    public = [{"id": "KSTH", "name": "South Field", "lon": DFW[1], "lat": DFW[0] - 0.1}]
    spots = find_hotspots(points(rows), 24.0, public, [])
    assert len(spots) == 1
    spot = spots[0]
    assert spot["aircraft"] == 3
    assert spot["top_class"] == "rotorcraft"
    assert spot["minutes_per_day"] == pytest.approx(20.0)
    assert spot["busiest_3h_start"] in (6, 7, 8)
    assert spot["nearest_public"]["dir"] == "N"
    # Measured from the centre of the busy cell, which can sit most of a mile from the points.
    assert spot["nearest_public"]["nm"] == pytest.approx(6.0, abs=0.8)
    assert spot["nearest_pad"] is None


def test_hotspot_needs_enough_time_and_aircraft():
    one_aircraft = points([{"icao": "solo", "dt": 5000.0}])
    assert find_hotspots(one_aircraft, 24.0, [], []) == []
    brief = points([{"icao": f"h{i}", "dt": 10.0} for i in range(5)])
    assert find_hotspots(brief, 24.0, [], []) == []


def test_link_shares_separate_single_band_from_dual_band():
    rows = [
        {"icao": "a", "dt": 900.0, "link": "adsb_1090"},
        {"icao": "u", "dt": 80.0, "link": "uat_or_adsr"},
        {"icao": "t", "dt": 15.0, "link": "tisb_only"},
        {"icao": "m", "dt": 5.0, "link": "mlat_only"},
    ]
    q5 = _q5(points(rows))
    assert q5["uat_share"] == pytest.approx(0.08)
    assert q5["dual_band_miss_share"] == pytest.approx(0.02)
    assert q5["single_band_miss_share"] == pytest.approx(0.10)
    assert sum(v["share"] for v in q5["by_link"].values()) == pytest.approx(1.0)
    assert q5["aircraft_by_link"]["uat_or_adsr"] == 1


def test_sparse_keeps_only_non_zero_bins():
    frame = points([{"dt": 12.4, "hour": 3}, {"dt": 0.2, "hour": 4}])
    code = {name: i for i, name in enumerate(CLASSES)}
    out = _sparse(frame, ["ac_class", "hour"], lambda cls, hour: code[cls] * 24 + int(hour))
    assert out[frame["h3"][0]] == [code["light"] * 24 + 3, 12]


def test_replay_breaks_a_track_at_a_gap_and_hides_private_aircraft():
    midnight = 1790658000.0  # 2026-09-29 00:00 in Dallas, as UTC seconds
    rows = [{"icao": "a1", "ts": midnight + 3600 + 10 * i, "lon": DFW[1] + 0.001 * i} for i in range(4)]
    rows += [{"icao": "a1", "ts": midnight + 7200 + 10 * i, "lon": DFW[1] + 0.001 * i} for i in range(3)]
    rows += [{"icao": "ladd", "ts": midnight + 3600 + 10 * i, "db_flags": 8} for i in range(4)]
    rows += [{"icao": "tooshort", "ts": midnight + 3600}]
    tracks = replay_tracks(points(rows), "2026-09-29")
    assert [len(t["p"]) // 4 for t in tracks] == [4, 3]
    assert tracks[0]["p"][0] == 3600
    assert tracks[0]["c"] == CLASSES.index("light")


def test_replay_thins_dense_reports():
    midnight = 1790658000.0
    rows = [{"icao": "a1", "ts": midnight + i} for i in range(60)]
    tracks = replay_tracks(points(rows), "2026-09-29")
    assert len(tracks[0]["p"]) // 4 == 6


def test_feed_health_flags_a_quiet_hour():
    start = 1790553600  # 2026-09-28 00:00 UTC
    rows = []
    for day, date in enumerate(["2026-09-28", "2026-09-29", "2026-09-30"]):
        busy = 1 if day == 1 else 12
        rows += [{"date": date, "ts": float(start + day * 86400 + 5 * 3600 + i)} for i in range(busy)]
    health = feed_health(points(rows), ["2026-09-28", "2026-09-29", "2026-09-30"])
    assert [d["points"] for d in health["days"]] == [12, 1, 12]
    assert [row["utc"] for row in health["thin_hours"]] == ["2026-09-29 05:00"]


def test_names():
    assert nice_name("MCKINNEY NTL") == "McKinney National"
    assert nice_name("EAGLE'S NEST ESTATES") == "Eagle's Nest Estates"
    assert nice_name("GARLAND/DFW HELOPLEX") == "Garland/DFW Heloplex"
    assert compass(0) == "N" and compass(44) == "NE" and compass(350) == "N" and compass(181) == "S"


class FlatTerrain:
    def sample_m(self, lon, lat):
        return np.full(np.shape(lon), 180.0)


@pytest.mark.skipif(not (ROOT / "data" / "layers" / "airports.geojson").exists(), reason="layers not fetched")
def test_every_public_airport_is_inside_the_stand_off():
    from shapely.geometry import shape

    from survey.geometry import operating_polygon
    from survey.metros import DFW
    from survey.zones import build_masks

    layers = ROOT / "data" / "layers"
    masks = build_masks(layers, FlatTerrain(), operating_polygon(DFW))
    airports = json.loads((layers / "airports.geojson").read_text())["features"]
    inside_area = [f for f in airports if f["properties"]["use"] == "PU" and masks["area"].covers(shape(f["geometry"]))]
    assert len(inside_area) > 40
    missing = [f["properties"]["id"] for f in inside_area if not masks["public_runway"].covers(shape(f["geometry"]))]
    assert missing == []
