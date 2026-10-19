"""Per-metro config, the traced Texas boundaries, and the area checks."""

import gzip
import json
from pathlib import Path

import numpy as np
import orjson
import pytest
from rasterio import features
from shapely.geometry import LineString, Polygon

from survey.geometry import area_check, operating_polygon
from survey.georef import (
    MAP_IMAGE,
    NASR_ZIP,
    TRACE_FILE,
    apply_affine,
    fit_affine,
    geodesic_area_sq_mi,
    match,
    offset_outline,
    trace_all,
    trace_outline,
)
from survey.metros import AUSTIN, DFW, METROS, SAN_ANTONIO
from survey.traces import parse_trace, parse_trace_boxes

ROOT = Path(__file__).resolve().parents[2]


def test_dfw_keeps_its_published_corners_and_file_locations():
    assert DFW.corners[3] == (-95.924358, 33.502911)
    assert DFW.stated_area_sq_mi == 10904
    assert DFW.raw_dir == ROOT / "data" / "raw"
    assert DFW.layers_dir == ROOT / "data" / "layers"
    assert DFW.cache == ROOT / "data" / "agl" / "prepared.parquet"
    assert DFW.web_dir == ROOT / "web" / "public" / "data"
    assert not DFW.traced


def test_other_metros_have_their_own_folders():
    assert AUSTIN.raw_dir == ROOT / "data" / "raw" / "austin"
    assert AUSTIN.web_dir == ROOT / "web" / "public" / "data" / "austin"
    assert len({metro.cache for metro in METROS.values()}) == len(METROS)


@pytest.mark.parametrize("metro", list(METROS.values()), ids=list(METROS))
def test_area_check_against_the_stated_area(metro):
    poly = operating_polygon(metro)
    assert poly.is_valid
    check = area_check(poly, metro.stated_area_sq_mi)
    assert check["stated_sq_mi"] == metro.stated_area_sq_mi
    assert check["within_half_percent"], check


@pytest.mark.parametrize("metro", list(METROS.values()), ids=list(METROS))
def test_terrain_and_geoid_cover_the_area(metro):
    lons = [c[0] for c in metro.corners]
    lats = [c[1] for c in metro.corners]

    def covered(lon, lat):
        # USGS tile nYYwXXX spans latitude YY-1 to YY and longitude -XXX to -XXX+1.
        for name in metro.dem_tiles:
            top, west = int(name[1:3]), -int(name[4:7])
            if top - 1 <= lat <= top and west <= lon <= west + 1:
                return True
        return False

    for lon in np.linspace(min(lons), max(lons), 9):
        for lat in np.linspace(min(lats), max(lats), 9):
            assert covered(lon, lat), (metro.key, lon, lat)
    lat0, lat1, lon0, lon1 = metro.geoid_box
    box = metro.bbox()
    assert lat0 <= box[1] and box[3] <= lat1 and lon0 <= box[0] and box[2] <= lon1


def test_bbox_pads_the_polygon():
    lon0, lat0, lon1, lat1 = AUSTIN.bbox()
    assert lon0 == pytest.approx(min(c[0] for c in AUSTIN.corners) - 0.15)
    assert lat1 == pytest.approx(max(c[1] for c in AUSTIN.corners) + 0.15)


def test_fit_affine_recovers_a_known_transform():
    rng = np.random.default_rng(1)
    A = np.array([[166.0, 3.0], [-2.0, -166.5], [500_000.0, 3_400_000.0]])
    src = rng.uniform(0, 1600, (40, 2))
    got = fit_affine(src, apply_affine(A, src))
    assert np.allclose(got, A)


def test_match_pairs_dots_despite_noise_decoys_and_a_rough_start():
    rng = np.random.default_rng(2)
    A = np.array([[166.0, 0.0], [0.0, -166.0], [400_000.0, 3_300_000.0]])
    pixels = rng.uniform(0, 1500, (60, 2))
    truth = apply_affine(A, pixels)
    # Reference list: the true sites in a shuffled order, plus sites the map does not draw.
    order = rng.permutation(60)
    ref = np.vstack([truth[order], apply_affine(A, rng.uniform(0, 1500, (40, 2)))])
    noisy = pixels + rng.normal(0, 0.4, pixels.shape)
    start = A.copy()
    start[2] += [1500.0, -1200.0]  # about 10 px off
    start[:2] *= 1.01
    fitted, dot_idx, ref_idx = match(noisy, ref, start)
    assert len(dot_idx) >= 55
    # Every pair is a true pair.
    assert all(order[r] == d for d, r in zip(dot_idx, ref_idx))
    assert np.abs(apply_affine(fitted, pixels) - truth).max() < 0.5 * 166


def outline_points(corners, width=3.0, shape=(700, 1400)):
    """Red pixels of a polygon outline drawn `width` pixels wide."""
    ring = LineString(list(corners) + [corners[0]]).buffer(width / 2)
    mask = features.rasterize([(ring, 1)], out_shape=shape, all_touched=False).astype(bool)
    ys, xs = np.nonzero(mask)
    # Pixel centres: rasterize's pixel (r, c) spans [c, c + 1], so its centre is c + 0.5.
    return np.column_stack([xs + 0.5, ys + 0.5]).astype(np.float64)


def test_trace_outline_finds_corners_to_half_a_pixel():
    truth = np.array([(833.4, 37.2), (1056.9, 14.1), (1223.6, 34.5), (1251.3, 607.5), (836.7, 610.4)])
    red = outline_points(truth)
    guess = truth + np.array([(2, -1), (-1, 1), (2, 2), (-2, 1), (1, -2)])
    refined, edges = trace_outline(red, guess)
    assert np.abs(refined - truth).max() < 0.5
    assert all(edge["pixels"] > 100 for edge in edges)


def test_trace_outline_can_ignore_part_of_an_edge():
    # Off the pixel grid, like a scanned map. A second outline runs alongside the
    # top edge, 4 px below it, for its first 250 px, as Austin's south edge runs
    # beside San Antonio's north edge.
    truth = np.array([(100.3, 100.2), (900.1, 101.4), (899.2, 500.3), (100.8, 499.6)])
    red = outline_points(truth)
    beside = outline_points(np.array([(60.0, 104.2), (350.0, 104.6), (350.0, 300.0), (60.0, 300.0)]))
    both = np.vstack([red, beside])
    loose, _ = trace_outline(both, truth)
    clean, _ = trace_outline(both, truth, {0: (380, 890)})
    # The corner is extrapolated from the part of the edge that is kept, so it is
    # less exact than a full edge, but the neighbour no longer pulls it.
    assert np.abs(clean - truth).max() < 1.0
    assert np.abs(loose - truth).max() > 1.5 * np.abs(clean - truth).max()


def test_offset_outline_moves_every_edge_out():
    square = np.array([(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)])
    grown = offset_outline(square, 1.0)
    assert Polygon(grown).area == pytest.approx(144.0)
    assert Polygon(offset_outline(square, -1.0)).area == pytest.approx(64.0)


def test_geodesic_area_of_a_one_degree_cell():
    # One degree square at the equator: 111.32 km by 110.57 km on WGS84, about 4,752 sq mi.
    cell = np.array([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    assert geodesic_area_sq_mi(cell) == pytest.approx(4752.4, rel=0.001)


def test_trace_file_matches_the_metro_corners():
    doc = json.loads(TRACE_FILE.read_text())
    assert doc["dots_matched"] >= 50
    assert doc["residual_rms_m"] < 200
    for metro in (AUSTIN, SAN_ANTONIO):
        entry = doc["outlines"][metro.key]
        assert np.allclose(entry["corners_lonlat"], metro.corners, atol=1e-6, rtol=0)
        assert entry["stated_sq_mi"] == metro.stated_area_sq_mi
        assert abs(entry["delta_pct"]) < 0.01
        lo, hi = entry["area_if_edges_move_1px_sq_mi"]
        assert lo < metro.stated_area_sq_mi < hi


@pytest.mark.skipif(not NASR_ZIP.exists() or not MAP_IMAGE.exists(), reason="NASR airports not downloaded")
def test_trace_reproduces_from_the_map():
    doc = trace_all(stated={"austin": 2498.0, "san_antonio": 3326.0})
    saved = json.loads(TRACE_FILE.read_text())
    for key in ("austin", "san_antonio"):
        assert np.allclose(doc["outlines"][key]["corners_lonlat"], saved["outlines"][key]["corners_lonlat"], atol=1e-6, rtol=0)


def test_boundary_summary_stops_when_corners_drift(tmp_path):
    import dataclasses

    from survey.run import boundary_summary

    summary = boundary_summary(AUSTIN)
    assert summary["method"] == "traced"
    assert summary["dots_matched"] >= 50
    moved = dataclasses.replace(AUSTIN, corners=((AUSTIN.corners[0][0] + 0.001, AUSTIN.corners[0][1]),) + AUSTIN.corners[1:])
    with pytest.raises(SystemExit):
        boundary_summary(moved)


def test_one_decode_gives_each_box_what_a_single_box_parse_gives():
    # Two boxes. The category changes on a point that lies only in the second box.
    # A single-box parse of the first box never sees that change, so the shared
    # decode must not apply it there either.
    trace = [
        [0, 30.0, -97.5, 500, 100, 0, 0, None, {"category": "A1", "t": "C172"}, "adsb_icao", 520],
        [10, 30.0, -97.4, 520, 100, 0, 0, None, None, "adsb_icao", 540],
        [20, 29.5, -98.5, 540, 100, 0, 0, None, {"category": "A7"}, "adsb_icao", 560],
        [30, 30.0, -97.3, "ground", 0, 0, 0, None, None, "adsb_icao", None],
    ]
    blob = gzip.compress(orjson.dumps({"icao": "abc123", "timestamp": 1790553600, "trace": trace}))
    boxes = {"north": (-97.6, 29.9, -97.2, 30.1), "south": (-98.6, 29.4, -97.2, 30.1)}
    together = parse_trace_boxes(blob, "prod", boxes)
    for name, box in boxes.items():
        assert together[name] == parse_trace(blob, "prod", box)
    assert [row["category"] for row in together["north"]] == ["A1", "A1", "A1"]
    assert [row["category"] for row in together["south"]] == ["A1", "A1", "A7", "A7"]
