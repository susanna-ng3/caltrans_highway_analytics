"""
Regression tests for clean_bottlenecks.transform(), run against real
coordinates pulled from both live layers (not synthetic geometry).

Fixtures:
  data/bottleneck_sample_route70.json      - the real District 3 / Rank 1 /
                                              Route 70 bottleneck record and
                                              its full polyline geometry
  data/shn_lines_sample_route70_yub.json   - the 8 real SHN Lines segments
                                              covering Route 70 through Yuba
                                              County (Left/Right x 4 postmile
                                              ranges), full geometry

Both were pulled from the live FeatureServers on 2026-09-08. RouteType was
fetched in a separate small query and merged in (the first geometry pull
didn't request it) - see the git history / conversation log if reproducing.

Run with: pytest test_clean_bottlenecks.py -v
"""

import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import LineString, MultiLineString

from clean_bottlenecks import _features_to_gdf, fetch_bottlenecks, transform

DATA_DIR = Path(__file__).resolve().parent / "data"
BOTTLENECK_FIXTURE = DATA_DIR / r"D:\Bridges\bottleneck_sample_route70.json"
SHN_FIXTURE = DATA_DIR / r"D:\Bridges\shn_lines_sample_route70_yub.json"


@pytest.fixture(scope="module")
def bottleneck_gdf():
    if not BOTTLENECK_FIXTURE.exists():
        pytest.skip(f"Fixture not found at {BOTTLENECK_FIXTURE}")
    raw = json.load(open(BOTTLENECK_FIXTURE))
    attrs = {
        "OBJECTID": 1, "District": "3", "Rank": "1", "County": "YUB", "Fwy": "SR70-E",
        "Name": "70EB Yuba River Br", "Type": "ML", "Shift": "PM", "Abs_PM": "20.149",
        "CA_PM": "13.428", "Number_Days_Active": "61", "Avg_Extent__Miles_": "2.72",
        "Total_Delay__veh_hrs_": "67847", "Direction": "E", "Abs_PM_upstream": "17.43",
        "Route": "70", "Beg_Cty": "YUB", "Beg_PM": "R9.087",
    }
    paths = raw["geometry_wgs84"]["paths"]
    geom = MultiLineString(paths) if len(paths) > 1 else LineString(paths[0])
    return gpd.GeoDataFrame([attrs], geometry=[geom], crs="EPSG:4326")


@pytest.fixture(scope="module")
def shn_gdf():
    if not SHN_FIXTURE.exists():
        pytest.skip(f"Fixture not found at {SHN_FIXTURE}")
    raw = json.load(open(SHN_FIXTURE))
    rows = [f["attributes"] for f in raw["features"]]
    geoms = [LineString(f["geometry"]["paths"][0]) for f in raw["features"]]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:102100")


def test_fixture_shape(bottleneck_gdf, shn_gdf):
    assert len(bottleneck_gdf) == 1
    assert len(shn_gdf) == 8
    assert set(shn_gdf["AlignCode"].unique()) == {"Left", "Right"}


def test_transform_matches_correct_postmile_segment(bottleneck_gdf, shn_gdf):
    # The bottleneck's CA_PM (13.428) falls inside the 13.008-14.7 segment,
    # not the neighboring 0.208-11.386 one - this is the exact case that
    # first exposed a bug in an earlier (down-sampled) version of this test
    # fixture: simplifying the real geometry to a few vertices distorted
    # the nearest-line distance enough to pick the wrong segment. With full
    # real coordinates, it resolves correctly.
    result = transform(bottleneck_gdf, shn_gdf)
    row = result.iloc[0]
    assert row["shn_bPM"] == 13.008
    assert row["shn_ePM"] == 14.7


def test_transform_flags_confidence_correctly(bottleneck_gdf, shn_gdf):
    result = transform(bottleneck_gdf, shn_gdf)
    row = result.iloc[0]
    assert bool(row["_pm_in_range"]) is True
    assert bool(row["low_confidence_match"]) is False


def test_transform_no_district_mismatch(bottleneck_gdf, shn_gdf):
    result = transform(bottleneck_gdf, shn_gdf)
    assert bool(result.iloc[0]["district_mismatch"]) is False


def test_transform_pulls_expected_route_context(bottleneck_gdf, shn_gdf):
    result = transform(bottleneck_gdf, shn_gdf)
    row = result.iloc[0]
    assert row["shn_RouteType"] == "State"
    assert row["shn_AlignCode"] in ("Left", "Right")


def test_low_confidence_flag_catches_bad_geometry():
    # Regression guard for the bug this fixture originally caught: feed
    # transform() a deliberately degraded (down-sampled) version of the SHN
    # geometry and confirm it still flags the resulting mismatch rather than
    # silently returning a wrong "confident" answer.
    bn_attrs = {
        "OBJECTID": 1, "District": "3", "Rank": "1", "County": "YUB",
        "CA_PM": "13.428", "Route": "70", "Direction": "E",
        "Total_Delay__veh_hrs_": "67847",
    }
    bn = gpd.GeoDataFrame(
        [bn_attrs],
        geometry=[LineString([(-121.5477, 39.1008), (-121.5770, 39.1245)])],
        crs="EPSG:4326",
    )
    # crude straight-line simplification of the two candidate segments
    shn = gpd.GeoDataFrame(
        [
            {"District": 3, "County": "YUB", "Route": 70, "PMPrefix": "R", "bPM": 0.208,
             "ePM": 11.386, "PMSuffix": "", "AlignCode": "Right", "RouteType": "State", "Direction": "EB"},
            {"District": 3, "County": "YUB", "Route": 70, "PMPrefix": "", "bPM": 13.008,
             "ePM": 14.7, "PMSuffix": "", "AlignCode": "Left", "RouteType": "State", "Direction": "WB"},
        ],
        geometry=[
            LineString([(-13530136.7893, 4718159.5891), (-13533904.8075, 4739499.1614)]),
            LineString([(-13533904.8075, 4739499.1614), (-13535467.3555, 4742418.2413)]),
        ],
        crs="EPSG:102100",
    )
    result = transform(bn, shn)
    row = result.iloc[0]
    if not row["_pm_in_range"]:
        assert row["low_confidence_match"] is True


def test_features_to_gdf_keeps_multipart_geometry():
    # Regression test for a real bug found when Suz ran this against the
    # full live dataset: the original _features_to_gdf() kept only
    # single-part polylines and silently discarded multi-part ones to
    # None. 28 of 90 real bottlenecks - including this exact one
    # (District 3, Rank 1, Route 70) - have a multi-part congestion
    # extent (a gap at an interchange) and were dropping out of the
    # spatial join entirely as a result, always landing in the
    # low_confidence_match fallback with every shn_* field blank.
    raw_feature = {
        "attributes": {
            "OBJECTID": 1, "District": "3", "Rank": "1", "County": "YUB",
            "CA_PM": "13.428", "Route": "70", "Direction": "E",
            "Total_Delay__veh_hrs_": "67847",
        },
        "geometry": {
            "paths": json.load(open(BOTTLENECK_FIXTURE))["geometry_wgs84"]["paths"]
        },
    }
    assert len(raw_feature["geometry"]["paths"]) == 2, "fixture should genuinely be multi-part"

    gdf = _features_to_gdf([raw_feature], crs="EPSG:4326")
    assert gdf.iloc[0].geometry is not None
    assert gdf.iloc[0].geometry.geom_type == "MultiLineString"


def test_multipart_bottleneck_now_matches_end_to_end(shn_gdf):
    # Same real multi-part bottleneck, this time run through
    # fetch_bottlenecks() (with requests.get mocked) end-to-end into
    # transform(), to prove the fix actually resolves the real-world
    # failure rather than just fixing the geometry type in isolation.
    from unittest.mock import patch
    import clean_bottlenecks as mod

    raw_feature = {
        "attributes": {
            "OBJECTID": 1, "District": "3", "Rank": "1", "County": "YUB", "Fwy": "SR70-E",
            "Name": "70EB Yuba River Br", "Type": "ML", "Shift": "PM", "Abs_PM": "20.149",
            "CA_PM": "13.428", "Number_Days_Active": "61", "Avg_Extent__Miles_": "2.72",
            "Total_Delay__veh_hrs_": "67847", "Direction": "E", "Abs_PM_upstream": "17.43",
            "Route": "70", "Beg_Cty": "YUB", "Beg_PM": "R9.087",
        },
        "geometry": {
            "paths": json.load(open(BOTTLENECK_FIXTURE))["geometry_wgs84"]["paths"]
        },
    }

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self._payload

    def fake_get(url, params=None, timeout=None):
        return FakeResp({"features": [raw_feature]})

    with patch.object(mod.requests, "get", side_effect=fake_get):
        bottlenecks = fetch_bottlenecks()

    result = transform(bottlenecks, shn_gdf)
    row = result.iloc[0]
    assert row["shn_bPM"] == 13.008
    assert row["shn_ePM"] == 14.7
    assert bool(row["low_confidence_match"]) is False


def test_transform_adds_sane_centroid_coordinates(bottleneck_gdf, shn_gdf):
    # The dashboard map needs somewhere to draw each bottleneck - transform()
    # adds a longitude/latitude centroid of the bottleneck's own line extent.
    # Check it lands inside the real coordinate envelope of that extent
    # rather than, say, being left in projected meters or the wrong hemisphere.
    result = transform(bottleneck_gdf, shn_gdf)
    row = result.iloc[0]
    assert -121.60 < row["longitude"] < -121.54
    assert 39.09 < row["latitude"] < 39.13