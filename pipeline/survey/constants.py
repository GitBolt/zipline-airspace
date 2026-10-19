"""Fixed definitions for the low-altitude surveillance survey.

Thresholds below are pre-committed. They are not tuned after looking at results.
They are the same for every metro; what differs between metros is in survey.metros.
"""

from __future__ import annotations

STUDY_DATES = [
    "2026-09-28",
    "2026-09-29",
    "2026-09-30",
    "2026-10-01",
    "2026-10-02",
    "2026-10-03",
    "2026-10-04",
]

REPLAY_DATE = "2026-09-29"
TIMEZONE = "America/Chicago"

BBOX_PAD_DEG = 0.15

# Altitude bands, feet AGL.
CRUISE_FT = 330.0
CRUISE_HALF_FT = 250.0
CRUISE_LO_FT = CRUISE_FT - CRUISE_HALF_FT  # 80
CRUISE_HI_FT = CRUISE_FT + CRUISE_HALF_FT  # 580
LOW_LO_FT = 0.0
LOW_HI_FT = 1200.0
ENROUTE_CEILING_FT = 400.0

# Points kept after height is known: ground reports and airborne points below this.
KEEP_BELOW_FT = 3000.0

# Reception. A cell counts as heard in a band when this many different aircraft
# were heard there below the top of the band.
MIN_SUPPORT = 3
MIN_DISTINCT = 3

# Airport reception check. A visit is an aircraft heard below VISIT_BELOW_FT
# within VISIT_RADIUS_NM of a runway end whose track starts, ends or breaks
# there, so it landed or took off and did not just fly past.
VISIT_RADIUS_NM = 1.5
VISIT_BELOW_FT = 1000.0
VISIT_BREAK_S = 120.0
VISIT_GROUND_FT = 100.0
MIN_VISITS = 5

# Busy areas away from public runways.
HOTSPOT_MIN_S = 600.0
HOTSPOT_MAX = 12

# Service radius of one site in the assessment, statute miles.
SITE_RADIUS_MI = 10.0

# Time integration.
GAP_CAP_S = 30.0
GAP_BREAK_S = 6 * 3600.0

# Zones.
RUNWAY_BUFFER_NM = 3.0
HELIPORT_BUFFER_NM = 1.0
NM_TO_M = 1852.0
RUNWAY_MATCH_M = 200.0

# Calibration and sensitivity.
SENSITIVITY_FT = 100.0
SHARE_FLIP_PP = 0.05
MIN_CAL_POINTS = 8
BASIS_MARGIN_FT = 25.0
RESIDUAL_BIN_FT = 25.0

H3_RES = 7

ADSB_SOURCES = {"adsb_icao", "adsb_icao_nt", "adsb_other"}
ADSR_SOURCES = {"adsr_icao", "adsr_other"}
TISB_SOURCES = {"tisb_icao", "tisb_other", "tisb_trackfile"}
MLAT_SOURCES = {"mlat"}

HELI_TYPES = {
    "R22", "R44", "R66", "B06", "B105", "B212", "B214", "B222", "B230",
    "B407", "B412", "B429", "B430", "B47G", "EC20", "EC25", "EC30", "EC35",
    "EC45", "EC55", "EC75", "A109", "A119", "A139", "A169", "A189", "S76",
    "S92", "H60", "AS50", "AS55", "AS65", "AS32", "H500", "H269", "MD52",
    "MD60", "NOTR", "B206", "UH1", "UH1H", "GAZL", "ALOU", "EXPL", "LYNX",
    "NH90", "CABR",
}
