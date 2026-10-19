"""Trace a study-area outline from the map in the FAA's Texas draft EA.

The draft EA for the Texas metros publishes their boundaries only as maps.
Appendix H (PDF page 194) draws every airport and heliport as a brown dot over
the red study-area outlines for Austin and San Antonio. The dots are matched to
FAA NASR coordinates to fit the map's projection, and each corner of an outline
is where straight lines fitted to the red pixels of its two edges meet. Every
step is a least-squares fit, so its error can be reported.

`python -m survey.georef` writes data/sources/texas_boundary_trace.json. The
corners in survey.metros are copied from that file, and a test checks they
still agree.
"""

from __future__ import annotations

import json
import warnings
import zipfile
from pathlib import Path

import numpy as np
import polars as pl
import rasterio
from pyproj import Geod, Transformer
from rasterio import features
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[2]
MAP_IMAGE = ROOT / "data" / "sources" / "texas_draft_ea_p194.jpg"
TRACE_FILE = ROOT / "data" / "sources" / "texas_boundary_trace.json"
NASR_ZIP = ROOT / "data" / "nasr" / "APT_CSV.zip"
SOURCE = (
    "FAA, Draft Environmental Assessment for Zipline's proposed drone package delivery in multiple "
    "Texas metropolitan areas, 11 September 2026, Appendix H (PDF page 194), map scale 1:1,025,400"
)
GEOD = Geod(ellps="WGS84")
SQ_M_PER_SQ_MI = 2_589_988.110336

# Projections tried for the map. The one with the smallest residual is used.
CANDIDATE_CRS = {
    "EPSG:3081": "Texas Centric Lambert Conformal",
    "EPSG:2277": "Texas Central state plane (Lambert)",
    "EPSG:3083": "Texas Centric Albers",
    "EPSG:26914": "UTM zone 14N",
    "EPSG:5070": "CONUS Albers",
    "EPSG:3857": "Web Mercator",
    "EPSG:4326": "Plate carree (longitude and latitude)",
}

# Hollow city markers on the map, read by eye, with the cities' coordinates.
# Only a starting guess for matching the airport dots.
CITY_MARKERS = {
    "Austin": ((1060.6, 338.1), (-97.7431, 30.2672)),
    "Cedar Park": ((1016.0, 177.0), (-97.8203, 30.5052)),
    "San Marcos": ((951.0, 594.0), (-97.9414, 29.8833)),
    "New Braunfels": ((847.0, 706.0), (-98.1245, 29.7030)),
    "Fredericksburg": ((405.0, 337.0), (-98.8720, 30.2752)),
    "Kerrville": ((255.0, 489.0), (-99.1403, 30.0474)),
    "La Grange": ((1569.0, 571.0), (-96.8767, 29.9055)),
}

# Outline corners read by eye, in drawing order, in pixels. The fit refines them.
# Austin's south edge and San Antonio's north edge are one line between x 836
# and 960, so each is fitted only where it runs alone.
OUTLINES = {
    "austin": {
        "corners": [(833, 37), (1057, 14), (1224, 35), (1252, 607), (835, 607)],
        "x_limits": {3: (975, 1260)},
    },
    "san_antonio": {
        "corners": [(336, 611), (965, 611), (965, 1112), (336, 1112)],
        "x_limits": {0: (340, 828)},
    },
}

# Region searched for NASR facilities: the whole map with a margin.
NASR_BOX = (-99.6, 28.7, -96.4, 31.2)

MATCH_RADII_PX = (25.0, 12.0, 6.0, 4.0, 3.0)
EDGE_HALF_WIDTH_PX = 5.0
EDGE_TRIM_PX = 14.0
BOOTSTRAP_RUNS = 500


def read_rgb(path: Path) -> np.ndarray:
    """Bands first: an array of shape (3, rows, cols)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # a plain JPEG has no geotransform
        with rasterio.open(path) as src:
            return src.read().astype(np.int16)


def dot_mask(img: np.ndarray) -> np.ndarray:
    """The brown fill of the airport symbol."""
    r, g, b = img
    return (
        (r >= 90) & (r <= 175) & (g >= 30) & (g <= 100) & (b >= 30) & (b <= 105)
        & (r - g >= 40) & (np.abs(g - b) <= 30)
    )


def red_mask(img: np.ndarray) -> np.ndarray:
    """The red study-area outline."""
    r, g, b = img
    return (r >= 160) & (g <= 80) & (b <= 80) & (r - g >= 110)


def blobs(mask: np.ndarray) -> np.ndarray:
    """Connected regions as rows of [pixels, x, y, width, height], x and y at pixel centres."""
    rows = []
    for geom, _ in features.shapes(mask.astype(np.uint8), mask=mask, connectivity=8):
        poly = shape(geom)
        x0, y0, x1, y1 = poly.bounds
        rows.append((poly.area, poly.centroid.x - 0.5, poly.centroid.y - 0.5, x1 - x0, y1 - y0))
    return np.array(rows, dtype=np.float64).reshape(-1, 5)


def single_dots(rows: np.ndarray) -> np.ndarray:
    """Centres of blobs the size and shape of one dot. Touching dots and fragments are left out."""
    area, w, h = rows[:, 0], rows[:, 3], rows[:, 4]
    keep = (area >= 15) & (area <= 55) & (w <= 10) & (h <= 10) & (np.abs(w - h) <= 2)
    return rows[keep, 1:3]


def fit_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares A, shape (3, 2), with dst = [x, y, 1] @ A."""
    design = np.column_stack([src, np.ones(len(src))])
    A, *_ = np.linalg.lstsq(design, dst, rcond=None)
    return A


def apply_affine(A: np.ndarray, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 2)
    return np.column_stack([pts, np.ones(len(pts))]) @ A


def unit_scale(A: np.ndarray) -> float:
    """Map units per pixel."""
    return float(np.sqrt(abs(np.linalg.det(A[:2]))))


def match(pixels: np.ndarray, ref: np.ndarray, A: np.ndarray, radii_px=MATCH_RADII_PX) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pair dots with reference points, refitting the transform as the search radius shrinks.

    A pair must be mutual nearest neighbours and closer than the radius. Returns
    the final transform and the indices of the paired dots and reference points.
    """
    for radius in radii_px:
        world = apply_affine(A, pixels)
        dist = np.sqrt(((world[:, None, :] - ref[None, :, :]) ** 2).sum(-1))
        nearest = dist.argmin(1)
        back = dist.argmin(0)
        ok = (dist[np.arange(len(pixels)), nearest] <= radius * unit_scale(A)) & (back[nearest] == np.arange(len(pixels)))
        if ok.sum() < 6:
            raise RuntimeError(f"only {int(ok.sum())} dots matched within {radius} px")
        A = fit_affine(pixels[ok], ref[nearest[ok]])
    return A, np.flatnonzero(ok), nearest[ok]


def fit_edge(red_pts: np.ndarray, a, b, x_limits=None, half=EDGE_HALF_WIDTH_PX, trim=EDGE_TRIM_PX) -> dict:
    """Total-least-squares line through the red pixels along the segment a-b.

    Pixels within `trim` of either end are left out so the corners do not pull
    the line. Returns a point on the line, its direction, the pixel count and the
    spread of the pixels across the line.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    length = float(np.linalg.norm(b - a))
    along = (b - a) / length
    normal = np.array([-along[1], along[0]])
    t = (red_pts - a) @ along
    s = (red_pts - a) @ normal
    keep = (np.abs(s) <= half) & (t >= trim) & (t <= length - trim)
    if x_limits is not None:
        keep &= (red_pts[:, 0] >= x_limits[0]) & (red_pts[:, 0] <= x_limits[1])
    pts = red_pts[keep]
    if len(pts) < 20:
        raise RuntimeError(f"edge {tuple(a)}-{tuple(b)} has only {len(pts)} red pixels")
    centre = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - centre)
    direction = vt[0]
    across = (pts - centre) @ np.array([-direction[1], direction[0]])
    return {"point": centre, "direction": direction, "pixels": int(len(pts)), "spread_px": float(across.std())}


def intersect(e1: dict, e2: dict) -> np.ndarray:
    m = np.column_stack([e1["direction"], -e2["direction"]])
    t = np.linalg.solve(m, e2["point"] - e1["point"])
    return e1["point"] + t[0] * e1["direction"]


def trace_outline(red_pts: np.ndarray, corners, x_limits: dict | None = None) -> tuple[np.ndarray, list[dict]]:
    """Refined corners, in the same order, and the fitted edges. Edge i runs from corner i to i+1."""
    x_limits = x_limits or {}
    n = len(corners)
    edges = [fit_edge(red_pts, corners[i], corners[(i + 1) % n], x_limits.get(i)) for i in range(n)]
    refined = np.array([intersect(edges[i - 1], edges[i]) for i in range(n)])
    return refined, edges


def offset_outline(vertices: np.ndarray, px: float) -> np.ndarray:
    """Move every edge `px` outward (negative: inward) and return the new corners."""
    n = len(vertices)
    centre = vertices.mean(0)
    lines = []
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        along = (b - a) / np.linalg.norm(b - a)
        normal = np.array([-along[1], along[0]])
        if (a - centre) @ normal < 0:
            normal = -normal
        lines.append({"point": a + px * normal, "direction": along})
    return np.array([intersect(lines[i - 1], lines[i]) for i in range(n)])


def geodesic_area_sq_mi(lonlat: np.ndarray) -> float:
    area, _ = GEOD.polygon_area_perimeter(lonlat[:, 0], lonlat[:, 1])
    return abs(area) / SQ_M_PER_SQ_MI


def nasr_facilities(zip_path: Path = NASR_ZIP, box=NASR_BOX) -> pl.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        base = pl.read_csv(zf.read("APT_BASE.csv"), infer_schema_length=10000)
    lon0, lat0, lon1, lat1 = box
    return base.filter(
        pl.col("LONG_DECIMAL").is_between(lon0, lon1) & pl.col("LAT_DECIMAL").is_between(lat0, lat1)
    ).select(["ARPT_ID", "SITE_TYPE_CODE", "LONG_DECIMAL", "LAT_DECIMAL"])


class MapFit:
    """Pixel to map-projection transform fitted from the airport dots."""

    def __init__(self, dots: np.ndarray, sites: pl.DataFrame, crs: str):
        self.crs = crs
        self.to_map = Transformer.from_crs(4326, crs, always_xy=True)
        self.to_lonlat = Transformer.from_crs(crs, 4326, always_xy=True)
        ref = np.column_stack(self.to_map.transform(sites["LONG_DECIMAL"].to_numpy(), sites["LAT_DECIMAL"].to_numpy()))
        city_px = np.array([px for px, _ in CITY_MARKERS.values()])
        city_ll = np.array([ll for _, ll in CITY_MARKERS.values()])
        start = fit_affine(city_px, np.column_stack(self.to_map.transform(city_ll[:, 0], city_ll[:, 1])))
        self.A, dot_idx, ref_idx = match(dots, ref, start)
        self.dots = dots[dot_idx]
        self.ref = ref[ref_idx]
        self.sites = sites[ref_idx]
        self.total_dots = len(dots)

    def lonlat(self, pixels: np.ndarray, A: np.ndarray | None = None) -> np.ndarray:
        world = apply_affine(self.A if A is None else A, pixels)
        return np.column_stack(self.to_lonlat.transform(world[:, 0], world[:, 1]))

    def residuals_m(self) -> np.ndarray:
        """Distance on the ground between each matched dot and its NASR position."""
        got = self.lonlat(self.dots)
        want = np.column_stack(self.to_lonlat.transform(self.ref[:, 0], self.ref[:, 1]))
        return np.array([GEOD.inv(g[0], g[1], w[0], w[1])[2] for g, w in zip(got, want)])

    def metres_per_px(self) -> float:
        a, b = self.lonlat(np.array([[800.0, 600.0], [900.0, 600.0]]))
        return GEOD.inv(a[0], a[1], b[0], b[1])[2] / 100.0

    def bootstrap(self, pixel_corners: np.ndarray, runs: int = BOOTSTRAP_RUNS, seed: int = 0) -> np.ndarray:
        """Largest corner shift, in metres, when the matched dots are resampled with replacement."""
        rng = np.random.default_rng(seed)
        base = self.lonlat(pixel_corners)
        out = []
        for _ in range(runs):
            pick = rng.integers(0, len(self.dots), len(self.dots))
            A = fit_affine(self.dots[pick], self.ref[pick])
            moved = self.lonlat(pixel_corners, A)
            out.append(max(GEOD.inv(p[0], p[1], q[0], q[1])[2] for p, q in zip(base, moved)))
        return np.array(out)


def _round(values, digits: int = 6):
    return [[round(float(x), digits) for x in row] for row in np.asarray(values)]


def trace_all(image: Path = MAP_IMAGE, nasr_zip: Path = NASR_ZIP, stated: dict | None = None) -> dict:
    """Fit the map, trace both outlines, and report errors and the area check."""
    img = read_rgb(image)
    dots = single_dots(blobs(dot_mask(img)))
    ys, xs = np.nonzero(red_mask(img))
    red_pts = np.column_stack([xs, ys]).astype(np.float64)
    sites = nasr_facilities(nasr_zip)
    outlines = {key: trace_outline(red_pts, spec["corners"], spec["x_limits"]) for key, spec in OUTLINES.items()}

    fits = {crs: MapFit(dots, sites, crs) for crs in CANDIDATE_CRS}
    projections = []
    for crs, fit in fits.items():
        res = fit.residuals_m()
        projections.append(
            {
                "crs": crs,
                "name": CANDIDATE_CRS[crs],
                "rms_px": float(np.sqrt(np.mean(((apply_affine(fit.A, fit.dots) - fit.ref) ** 2).sum(1))) / unit_scale(fit.A)),
                "rms_m": float(np.sqrt(np.mean(res**2))),
            }
        )
    projections.sort(key=lambda row: row["rms_px"])
    best = fits[projections[0]["crs"]]
    close = [row["crs"] for row in projections if row["rms_px"] <= 1.1 * projections[0]["rms_px"]]

    res = best.residuals_m()
    m_per_px = best.metres_per_px()
    out = {
        "source": SOURCE,
        "image": str(image.relative_to(ROOT)) if image.is_relative_to(ROOT) else str(image),
        "crs": best.crs,
        "crs_name": CANDIDATE_CRS[best.crs],
        "metres_per_px": m_per_px,
        "dots_found": int(best.total_dots),
        "dots_matched": int(len(best.dots)),
        "matched_by_type": {k: int(v) for k, v in best.sites.group_by("SITE_TYPE_CODE").len().sort("SITE_TYPE_CODE").iter_rows()},
        "residual_rms_m": float(np.sqrt(np.mean(res**2))),
        "residual_median_m": float(np.median(res)),
        "residual_max_m": float(res.max()),
        "projections": projections,
        "outlines": {},
    }
    for key, (pixel_corners, edges) in outlines.items():
        lonlat = best.lonlat(pixel_corners)
        spread = best.bootstrap(pixel_corners)
        alt_shift = 0.0
        for crs in close:
            other = fits[crs].lonlat(pixel_corners)
            alt_shift = max(alt_shift, max(GEOD.inv(p[0], p[1], q[0], q[1])[2] for p, q in zip(lonlat, other)))
        computed = geodesic_area_sq_mi(lonlat)
        grown = geodesic_area_sq_mi(best.lonlat(offset_outline(pixel_corners, 1.0)))
        shrunk = geodesic_area_sq_mi(best.lonlat(offset_outline(pixel_corners, -1.0)))
        entry = {
            "corners_px": _round(pixel_corners, 2),
            "corners_lonlat": _round(lonlat, 6),
            "edges": [{"pixels": e["pixels"], "spread_px": round(e["spread_px"], 3)} for e in edges],
            "corner_shift_bootstrap_p95_m": float(np.quantile(spread, 0.95)),
            "corner_shift_other_projections_m": float(alt_shift),
            "computed_sq_mi": computed,
            "area_if_edges_move_1px_sq_mi": [shrunk, grown],
        }
        if stated and key in stated:
            entry["stated_sq_mi"] = stated[key]
            entry["delta_pct"] = (computed - stated[key]) / stated[key]
        out["outlines"][key] = entry
    return out


def main() -> None:
    stated = {"austin": 2498.0, "san_antonio": 3326.0}
    doc = trace_all(stated=stated)
    TRACE_FILE.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"map projection {doc['crs']} ({doc['crs_name']}), {doc['metres_per_px']:.1f} m per pixel")
    print(f"dots {doc['dots_matched']} of {doc['dots_found']} matched, residual rms {doc['residual_rms_m']:.0f} m, max {doc['residual_max_m']:.0f} m")
    for row in doc["projections"]:
        print(f"  {row['crs']:<10} {row['rms_px']:.3f} px  {row['rms_m']:.0f} m  {row['name']}")
    for key, entry in doc["outlines"].items():
        lo, hi = entry["area_if_edges_move_1px_sq_mi"]
        print(
            f"{key}: {entry['computed_sq_mi']:.1f} sq mi against {entry.get('stated_sq_mi')} stated "
            f"({100 * entry.get('delta_pct', 0):+.2f}%), {lo:.0f}-{hi:.0f} if every edge moves 1 px; "
            f"corners move {entry['corner_shift_bootstrap_p95_m']:.0f} m (95% bootstrap), "
            f"{entry['corner_shift_other_projections_m']:.0f} m across close projections"
        )
        print("  ", entry["corners_lonlat"])


if __name__ == "__main__":
    main()
