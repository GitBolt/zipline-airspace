"""One entry per surveyed metro: its boundary, where its files live, and its local sources.

Dallas–Fort Worth's boundary is the published quadrilateral in its final
environmental assessment. Austin and San Antonio are traced from the map in the
Texas metros draft assessment (see survey.georef), because no coordinates are
published for them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from survey.constants import BBOX_PAD_DEG

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Metro:
    key: str
    name: str
    survey_name: str
    # Name of the boundary feature in the layer files.
    area_label: str
    corners: tuple[tuple[float, float], ...]
    stated_area_sq_mi: float
    boundary_note: str
    metar_stations: tuple[str, ...]
    dem_tiles: dict[str, int]
    # (lon, lat) of the airport that reception distances are measured from.
    hub: tuple[float, float]
    hub_id: str
    # GEOID18 grid: (lat0, lat1, lon0, lon1), 0.2 degree step.
    geoid_box: tuple[float, float, float, float]
    # "" keeps Dallas–Fort Worth's files where they were before there was more than one metro.
    folder: str = ""
    traced: bool = False
    bbox_pad_deg: float = BBOX_PAD_DEG

    def bbox(self) -> tuple[float, float, float, float]:
        lons = [c[0] for c in self.corners]
        lats = [c[1] for c in self.corners]
        pad = self.bbox_pad_deg
        return (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)

    def _under(self, base: Path) -> Path:
        return base / self.folder if self.folder else base

    @property
    def raw_dir(self) -> Path:
        return self._under(ROOT / "data" / "raw")

    @property
    def layers_dir(self) -> Path:
        return self._under(ROOT / "data" / "layers")

    @property
    def cache(self) -> Path:
        return self._under(ROOT / "data" / "agl") / "prepared.parquet"

    @property
    def web_dir(self) -> Path:
        return self._under(ROOT / "web" / "public" / "data")


DFW = Metro(
    key="dfw",
    name="Dallas–Fort Worth",
    survey_name="DFW Low-Altitude Surveillance Survey",
    area_label="DFW operating area",
    # Footnote 12 of the final assessment. The printed NE corner longitude is missing its decimal point.
    corners=(
        (-97.976603, 33.502972),
        (-97.976692, 32.178781),
        (-95.923964, 32.179006),
        (-95.924358, 33.502911),
    ),
    stated_area_sq_mi=10904.0,
    boundary_note="The printed NE longitude -95924358 is read as -95.924358.",
    metar_stations=(
        "DFW", "DAL", "AFW", "ADS", "FTW", "DTO", "GKY", "TKI", "FWS", "RBD",
        "LNC", "GPM", "HQZ", "GVT", "MWL", "SEP", "ACT", "NFW",
    ),
    dem_tiles={
        "n33w098": 54473476,
        "n33w097": 53318809,
        "n33w096": 56057619,
        "n34w098": 54021363,
        "n34w097": 52958494,
        "n34w096": 53940902,
    },
    hub=(-97.0380, 32.8968),
    hub_id="DFW",
    geoid_box=(32.0, 33.8, -98.3, -95.7),
)

AUSTIN = Metro(
    key="austin",
    name="Austin",
    survey_name="Austin Low-Altitude Surveillance Survey",
    area_label="Austin proposed operating area",
    # Traced from Appendix H of the draft assessment: data/sources/texas_boundary_trace.json.
    corners=(
        (-98.134311, 30.716384),
        (-97.746745, 30.747555),
        (-97.458098, 30.713819),
        (-97.423834, 29.855524),
        (-98.136095, 29.858212),
    ),
    stated_area_sq_mi=2498.0,
    boundary_note=(
        "Traced from the map in Appendix H of the draft assessment. No coordinates are published."
    ),
    metar_stations=("AUS", "ATT", "EDC", "GTU", "HYI", "RYW", "T74", "BMQ", "BAZ", "DZB", "GYB", "SEQ"),
    dem_tiles={
        "n30w099": 55144264,
        "n30w098": 56750132,
        "n31w099": 54655786,
        "n31w098": 55290513,
    },
    hub=(-97.6699, 30.1945),
    hub_id="AUS",
    geoid_box=(29.6, 31.0, -98.4, -97.2),
    folder="austin",
    traced=True,
)

SAN_ANTONIO = Metro(
    key="san_antonio",
    name="San Antonio",
    survey_name="San Antonio Low-Altitude Surveillance Survey",
    area_label="San Antonio proposed operating area",
    corners=(
        (-98.995209, 29.860113),
        (-97.924845, 29.856692),
        (-97.92532, 29.107067),
        (-98.988538, 29.107334),
    ),
    stated_area_sq_mi=3326.0,
    boundary_note=(
        "Traced from the map in Appendix H of the draft assessment. No coordinates are published."
    ),
    metar_stations=(
        "SAT", "SSF", "SKF", "RND", "5C1", "CVB", "HDO", "ERV", "BAZ", "HYI", "SEQ", "PEZ", "T82", "T20",
    ),
    dem_tiles={
        "n29w100": 53447300,
        "n29w099": 55755966,
        "n29w098": 56253487,
        "n30w100": 54792386,
        "n30w099": 55144264,
        "n30w098": 56750132,
        "n31w100": 53386164,
        "n31w099": 54655786,
        "n31w098": 55290513,
    },
    hub=(-98.4698, 29.5337),
    hub_id="SAT",
    geoid_box=(28.8, 30.2, -99.4, -97.6),
    folder="san_antonio",
    traced=True,
)

METROS = {metro.key: metro for metro in (DFW, AUSTIN, SAN_ANTONIO)}


def get(key: str) -> Metro:
    try:
        return METROS[key]
    except KeyError:
        raise SystemExit(f"unknown metro {key!r}; choose from {', '.join(METROS)}") from None
