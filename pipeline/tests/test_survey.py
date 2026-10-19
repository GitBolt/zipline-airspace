from survey.altitude import agl_from_barometric, agl_from_geometric, choose_basis
from survey.classify import aircraft_class, capped_dt, link_class, normalize_source
from survey.geometry import area_check, operating_polygon
from survey.metros import DFW


def test_gap_cap():
    assert capped_dt(10) == 10
    assert capped_dt(90) == 30
    assert capped_dt(0) == 0
    assert capped_dt(None) == 0
    assert capped_dt(7 * 3600) == 0


def test_link_class_adsb_wins_over_mlat():
    assert link_class({"mlat", "adsb_icao"}) == "adsb_1090"
    assert link_class({"adsr_icao"}) == "uat_or_adsr"
    assert link_class({"tisb_trackfile"}) == "tisb_only"
    assert link_class({"mlat"}) == "mlat_only"
    assert link_class({"other"}) == "other"


def test_mlat_pod_other_is_mlat():
    assert normalize_source("other", "mlatonly") == "mlat"
    assert normalize_source("other", "prod") == "other"
    assert normalize_source("adsb_icao", "mlatonly") == "adsb_icao"


def test_classes():
    assert aircraft_class("A0", None) == "unknown"
    assert aircraft_class("A7", "B738") == "rotorcraft"
    assert aircraft_class("A1", None) == "light"
    assert aircraft_class("A3", None) == "large"
    assert aircraft_class("A0", "R44") == "rotorcraft"
    assert aircraft_class("B6", None) is None
    assert aircraft_class("C1", None) is None


def test_geoid_sign():
    # N = -27 m. Orthometric = HAE - N, so AGL is about 89 ft higher than HAE-minus-terrain.
    agl = agl_from_geometric(
        __import__("numpy").array([1000.0]),
        __import__("numpy").array([100.0]),
        __import__("numpy").array([-27.0]),
        False,
    )
    terrain_ft = 100 * 3.280839895
    geoid_ft = -27 * 3.280839895
    expected = 1000 - geoid_ft - terrain_ft
    assert abs(float(agl[0]) - expected) < 0.01
    assert float(agl[0]) > 1000 - terrain_ft


def test_barometric_correction():
    agl = agl_from_barometric(
        __import__("numpy").array([1000.0]),
        __import__("numpy").array([0.0]),
        __import__("numpy").array([30.12]),
    )
    assert abs(float(agl[0]) - 1200.0) < 0.01


def test_basis_prefers_tighter_residual():
    import numpy as np

    hae = np.array([80.0, 90.0, 70.0])
    msl = np.array([5.0, -4.0, 6.0])
    assert choose_basis(hae, msl) == "msl"
    assert choose_basis(msl, hae) == "hae"


def test_operating_area_is_the_published_box():
    poly = operating_polygon(DFW)
    assert poly.is_valid
    check = area_check(poly, DFW.stated_area_sq_mi)
    assert 10_000 < check["computed_sq_mi"] < 12_000
    assert check["stated_sq_mi"] == 10904
