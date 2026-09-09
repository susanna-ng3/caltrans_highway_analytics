"""
Tests for clean_traffic_volume.py.

Two tiers, matching the pattern used for the other clean_*.py scripts:
  - Small unit tests built from real rows/segments pulled from the live
    layers (hand-copied below, with a source note on each) - these pin down
    exact expected behavior for the tricky logic: unpivoting, dedup, and
    the District+County+Route+postmile RouteType join.
  - Full-pipeline sanity tests against the complete real downloads Suz
    provided (data/Annual_Average_Daily_Traffic_raw.csv,
    data/Truck_Average_Daily_Traffic_raw.csv, data/SHN_Lines_raw_sample.csv)
    - skipped if those files aren't present.

fetch_aadt()/fetch_truck_aadt() ask the live FeatureServer for point
geometry already reprojected to EPSG:4326 (outSR=4326), so every row that
reaches transform_aadt()/transform_truck() already has longitude/latitude
columns. The raw CSVs Suz provided only have the layers' native X/Y
(confirmed to be EPSG:2230 - see clean_traffic_volume.py's docstring), so
the full-pipeline tests reproject those with pyproj to build a stand-in for
what the live query would actually return - this sandbox can't reach the
ArcGIS endpoint directly (same restriction as earlier in this project), so
that reprojection is the only way to exercise the full real dataset here.
"""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from clean_traffic_volume import (
    AADT_FIELDS,
    AADT_URL,
    TRUCK_FIELDS,
    _add_rank_and_percentile,
    _attach_route_type,
    _drop_exact_duplicates,
    _features_to_point_df,
    _paged_query,
    _unpivot_aadt,
    fetch_aadt,
    transform_aadt,
    transform_truck,
)

DATA_DIR = Path(__file__).resolve().parent / "data"


# ---------------------------------------------------------------- fixtures --

@pytest.fixture
def las_cruces_row():
    """Real AADT row, District 5 / Route 1 / PM 0.001 (OBJECTID 1, pulled 2026-09-08)."""
    return pd.DataFrame([{
        "OBJECTID": 1, "DISTRICT": 5, "RTE": 1, "RTE_SFX": np.nan, "CNTY": "SB",
        "PM_PFX": "R", "PM": 0.001, "PM_SFX": np.nan,
        "DESCRIPTION": "LAS CRUCES, JCT. RTE. 101; MOBIL OIL PIER",
        "BACK_PEAK_HOUR": 40.0, "BACK_PEAK_MADT": 600.0, "BACK_AADT": 540.0,
        "AHEAD_PEAK_HOUR": 860.0, "AHEAD_PEAK_MADT": 8000.0, "AHEAD_AADT": 7700.0,
        "longitude": -120.601, "latitude": 34.469,
    }])


@pytest.fixture
def null_back_aadt_row():
    """Real AADT row with a blank BACK_AADT (OBJECTID 3, District 11/Route 5/San Diego Co.)."""
    return pd.DataFrame([{
        "OBJECTID": 3, "DISTRICT": 11, "RTE": 5, "RTE_SFX": np.nan, "CNTY": "SD",
        "PM_PFX": "R", "PM": 30.345, "PM_SFX": "R", "DESCRIPTION": "BEGIN RIGHT ALIGN",
        "BACK_PEAK_HOUR": np.nan, "BACK_PEAK_MADT": np.nan, "BACK_AADT": np.nan,
        "AHEAD_PEAK_HOUR": 5900.0, "AHEAD_PEAK_MADT": 69000.0, "AHEAD_AADT": 66000.0,
        "longitude": -117.16, "latitude": 32.63,
    }])


@pytest.fixture
def route1_district5_sb_shn():
    """Real SHN Lines rows for Route 1 / District 5 / Santa Barbara Co. (pulled 2026-09-08).

    The Right/Right-Independent rows alone cover PM 0.001 exactly once
    (bPM=0.0, ePM=7.606) - this is the fixture that proves District+County
    are needed: Route 1 alone has 11 same-PM candidates spread across 5
    districts (see module docstring), and this narrows it to one.
    """
    rows = [
        (5, "SB", 1, "", 40.023, 50.606, "", "Right", "State"),
        (5, "SB", 1, "R", 31.042, 36.5, "", "Right", "State"),
        (5, "SB", 1, "M", 29.0, 36.189, "", "Right", "State"),
        (5, "SB", 1, "R", 23.296, 29.0, "", "Right", "State"),
        (5, "SB", 1, "R", 12.505, 13.206, "", "Right", "State"),
        (5, "SB", 1, "R", 0.0, 7.606, "", "Right", "State"),
        (5, "SB", 1, "", 9.02, 12.505, "", "Right", "State"),
        (5, "SB", 1, "", 13.219, 23.296, "", "Right", "State"),
        (5, "SB", 1, "", 40.023, 50.606, "", "Left", "State"),
        (5, "SB", 1, "M", 29.0, 36.189, "", "Left", "State"),
    ]
    cols = ["District", "County", "Route", "PMPrefix", "bPM", "ePM", "PMSuffix", "AlignCode", "RouteType"]
    return pd.DataFrame(rows, columns=cols)


@pytest.fixture
def route273_district2_shasta_shn():
    """Real SHN Lines rows for Route 273 / District 2 / Shasta Co. (pulled 2026-09-08).

    Right/Right-Independent coverage has a genuine gap at PM 16.801 -
    [3.812-15.921] and [16.833-20.033] - only Left Independent
    (bPM=16.255, ePM=16.801) covers that exact postmile. This is the real
    case that motivated the per-row any-AlignCode fallback.
    """
    rows = [
        (2, "SHA", 273, "", 16.833, 20.033, "", "Right", "State"),
        (2, "SHA", 273, "", 15.921, 16.255, "R", "Right Independent", "State"),
        (2, "SHA", 273, "", 16.833, 20.033, "", "Left", "State"),
        (2, "SHA", 273, "R", 16.255, 16.801, "L", "Left Independent", "State"),
        (2, "SHA", 273, "", 16.184, 16.255, "L", "Left Independent", "State"),
        (2, "SHA", 273, "", 15.921, 16.159, "L", "Left Independent", "State"),
        (2, "SHA", 273, "", 3.812, 15.921, "", "Left", "State"),
        (2, "SHA", 273, "", 3.812, 15.921, "", "Right", "State"),
        (2, "SHA", 273, "R", 16.255, 16.73, "R", "Right Independent", "State"),
    ]
    cols = ["District", "County", "Route", "PMPrefix", "bPM", "ePM", "PMSuffix", "AlignCode", "RouteType"]
    return pd.DataFrame(rows, columns=cols)


def _full_fixture_paths():
    return (
        DATA_DIR / "Annual_Average_Daily_Traffic_raw.csv",
        DATA_DIR / "Truck_Average_Daily_Traffic_raw.csv",
        DATA_DIR / "SHN_Lines_raw_sample.csv",
    )


def _skip_if_missing(*paths):
    for p in paths:
        if not p.exists():
            pytest.skip(f"Fixture not found at {p}")


def _reproject_xy_to_lonlat(df: pd.DataFrame) -> pd.DataFrame:
    """Test-only stand-in for the live query's outSR=4326 geometry.

    See module docstring - the raw CSVs only carry native X/Y (EPSG:2230),
    so this reprojects them with pyproj to build what fetch_aadt()/
    fetch_truck_aadt() would actually hand back.
    """
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:2230", "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(df["X"].values, df["Y"].values)
    df = df.copy()
    df["longitude"] = lon
    df["latitude"] = lat
    return df


@pytest.fixture
def full_aadt_input():
    aadt_path, _, _ = _full_fixture_paths()
    _skip_if_missing(aadt_path)
    raw = pd.read_csv(aadt_path)
    raw = _reproject_xy_to_lonlat(raw)
    return raw[AADT_FIELDS + ["longitude", "latitude"]]


@pytest.fixture
def full_truck_input():
    _, truck_path, _ = _full_fixture_paths()
    _skip_if_missing(truck_path)
    raw = pd.read_csv(truck_path)
    raw = _reproject_xy_to_lonlat(raw)
    return raw[TRUCK_FIELDS + ["longitude", "latitude"]]


@pytest.fixture
def full_shn():
    """Real full SHN Lines download, restricted to exactly SHN_FIELDS.

    This must mirror fetch_shn_route_types()'s outFields, not the full raw
    CSV's columns - a real bug (SHN_FIELDS missing "County", causing a
    KeyError in production on Suz's live run) slipped past this test suite
    because an earlier version of this fixture handed transform_aadt() the
    complete raw CSV, which happens to have every column regardless of what
    the live query actually asks for. Restricting to SHN_FIELDS here is
    what would have caught it.
    """
    _, _, shn_path = _full_fixture_paths()
    _skip_if_missing(shn_path)
    from clean_traffic_volume import SHN_FIELDS

    return pd.read_csv(shn_path)[SHN_FIELDS]


# ---------------------------------------------------------- unit: dedup --

def test_drop_exact_duplicates_removes_full_row_repeat():
    df = pd.DataFrame([
        {"OBJECTID": 1, "RTE": 1, "AADT": 100},
        {"OBJECTID": 2, "RTE": 1, "AADT": 100},  # exact repeat of row 1 except OBJECTID
        {"OBJECTID": 3, "RTE": 2, "AADT": 200},
    ])
    out = _drop_exact_duplicates(df)
    assert len(out) == 2
    assert sorted(out["OBJECTID"].tolist()) == [1, 3]


def test_drop_exact_duplicates_keeps_genuinely_different_rows():
    df = pd.DataFrame([
        {"OBJECTID": 1, "RTE": 1, "AADT": 100},
        {"OBJECTID": 2, "RTE": 1, "AADT": 150},  # different AADT - not a duplicate
    ])
    out = _drop_exact_duplicates(df)
    assert len(out) == 2


def test_drop_exact_duplicates_ignores_coordinate_jitter(full_aadt_input, full_truck_input):
    # These exact counts were confirmed against the real full downloads.
    # Dropping duplicates while IGNORING coordinates (13,919 -> 7,126 for
    # AADT; 6,865 -> 3,461 for Truck AADT) collapses a real pattern found
    # while testing: divided highways where both carriageways are digitized
    # as separate points a few dozen feet apart but carry the identical
    # traffic reading (agreed with Suz, 2026-09-09, after confirming the
    # coordinate difference in these pairs is consistently under ~0.3 mi -
    # carriageway-offset jitter, not a genuinely different location).
    aadt_deduped = _drop_exact_duplicates(full_aadt_input.assign(OBJECTID=range(len(full_aadt_input))))
    assert len(full_aadt_input) == 13919
    assert len(aadt_deduped) == 7126

    truck_deduped = _drop_exact_duplicates(full_truck_input.assign(OBJECTID=range(len(full_truck_input))))
    assert len(full_truck_input) == 6865
    assert len(truck_deduped) == 3461


def test_drop_exact_duplicates_collapses_real_divided_highway_pair():
    # Real District 7 / Route 10 / PM 14.25 "Back" pair (OBJECTID 10896 and
    # 10897, Los Angeles - Hoover Street) - identical in every attribute
    # column but sit ~13 ft apart (the two carriageways of I-10). Without
    # ignoring coordinates these show up as two distinct "busiest location"
    # bars for the same real intersection.
    df = pd.DataFrame([
        {"OBJECTID": 10896, "DISTRICT": 7, "RTE": 10, "CNTY": "LA", "PM": 14.25,
         "DESCRIPTION": "LOS ANGELES, HOOVER STREET", "BACK_AADT": 345000.0,
         "longitude": -118.283996, "latitude": 34.037040},
        {"OBJECTID": 10897, "DISTRICT": 7, "RTE": 10, "CNTY": "LA", "PM": 14.25,
         "DESCRIPTION": "LOS ANGELES, HOOVER STREET", "BACK_AADT": 345000.0,
         "longitude": -118.284004, "latitude": 34.037221},
    ])
    out = _drop_exact_duplicates(df)
    assert len(out) == 1
    assert out.iloc[0]["OBJECTID"] == 10896  # keeps the first occurrence


# -------------------------------------------------------- unit: unpivot --

def test_unpivot_produces_two_rows_with_correct_values(las_cruces_row):
    out = _unpivot_aadt(las_cruces_row)
    assert len(out) == 2
    assert set(out["direction"]) == {"Back", "Ahead"}

    back = out[out["direction"] == "Back"].iloc[0]
    assert back["AADT"] == 540.0
    assert back["peak_hour_volume"] == 40.0
    assert back["peak_month_adt"] == 600.0

    ahead = out[out["direction"] == "Ahead"].iloc[0]
    assert ahead["AADT"] == 7700.0
    assert ahead["peak_hour_volume"] == 860.0
    assert ahead["peak_month_adt"] == 8000.0

    # shared identifying columns should be identical on both rows
    for col in ("DISTRICT", "RTE", "CNTY", "PM", "DESCRIPTION"):
        assert back[col] == ahead[col]


def test_unpivot_drops_the_missing_side_only(null_back_aadt_row):
    out = _unpivot_aadt(null_back_aadt_row)
    # BACK_AADT is null on this real row - only the Ahead reading should survive
    assert len(out) == 1
    assert out.iloc[0]["direction"] == "Ahead"
    assert out.iloc[0]["AADT"] == 66000.0


# ------------------------------------------------- unit: RouteType join --

def test_attach_route_type_needs_district_and_county_to_disambiguate(route1_district5_sb_shn):
    """Route 1 alone has candidates in 5 different districts covering PM 0 -
    without District+County in the match key this could silently pick the
    wrong district's segment. Confirms it resolves to the one real segment
    that actually covers District 5 / Santa Barbara Co. / PM 0.001."""
    row = pd.DataFrame([{
        "RTE": 1, "DISTRICT": 5, "CNTY": "SB", "PM": 0.001, "PM_PFX": "R",
    }])
    out = _attach_route_type(
        row, route1_district5_sb_shn, pm_col="PM", pm_pfx_col="PM_PFX",
        route_col="RTE", district_col="DISTRICT", cnty_col="CNTY",
    )
    assert out.iloc[0]["route_type_matched"]
    assert out.iloc[0]["RouteType"] == "State"


def test_attach_route_type_falls_back_to_any_align_code_for_coverage_gap(route273_district2_shasta_shn):
    """Real Route 273/District 2/Shasta Co. case: PM 16.801 sits in a gap in
    Right/Right-Independent coverage and is only covered by a Left
    Independent segment - this is the case that motivated the per-row
    (not per-group) AlignCode fallback."""
    row = pd.DataFrame([{
        "RTE": 273, "DISTRICT": 2, "CNTY": "SHA", "PM": 16.801, "PM_PFX": "R",
    }])
    out = _attach_route_type(
        row, route273_district2_shasta_shn, pm_col="PM", pm_pfx_col="PM_PFX",
        route_col="RTE", district_col="DISTRICT", cnty_col="CNTY",
    )
    assert out.iloc[0]["route_type_matched"]
    assert out.iloc[0]["RouteType"] == "State"


def test_attach_route_type_flags_unmatched_when_no_candidates_exist(route1_district5_sb_shn):
    # District/County/Route combination that doesn't exist anywhere in the
    # (small) fixture - a synthetic negative case for the empty-candidates guard.
    row = pd.DataFrame([{
        "RTE": 999, "DISTRICT": 5, "CNTY": "ZZ", "PM": 1.0, "PM_PFX": np.nan,
    }])
    out = _attach_route_type(
        row, route1_district5_sb_shn, pm_col="PM", pm_pfx_col="PM_PFX",
        route_col="RTE", district_col="DISTRICT", cnty_col="CNTY",
    )
    assert not out.iloc[0]["route_type_matched"]
    assert pd.isna(out.iloc[0]["RouteType"])


def test_attach_route_type_survives_string_vs_numeric_field_types(route1_district5_sb_shn):
    # Real failure on Suz's first live run: the join matched 0 of 12,994
    # Annual AADT rows even though Truck AADT (same SHN data) matched
    # 99.9%. Annual AADT and SHN Lines are two separately published
    # FeatureServer layers - the leading theory is their schemas declare
    # Route/District with different field types (e.g. text vs numeric), so
    # a live "5" never equalled a live 5 and every group came back empty.
    # This reproduces that mismatch directly: Route/District as strings on
    # the AADT side, ints in the SHN fixture.
    row = pd.DataFrame([{
        "RTE": "1", "DISTRICT": "5", "CNTY": " sb ", "PM": 0.001, "PM_PFX": "R",
    }])
    out = _attach_route_type(
        row, route1_district5_sb_shn, pm_col="PM", pm_pfx_col="PM_PFX",
        route_col="RTE", district_col="DISTRICT", cnty_col="CNTY",
    )
    assert out.iloc[0]["route_type_matched"]
    assert out.iloc[0]["RouteType"] == "State"


def test_attach_route_type_survives_pm_as_a_string(route1_district5_sb_shn):
    # Second real failure, one layer deeper than the Route/District/County
    # mismatch above: once that fix let candidate-filtering actually
    # succeed, Suz's next live run crashed inside the bPM/ePM containment
    # comparison with "Invalid comparison between dtype=float64 and str" -
    # the live Annual AADT query returns PM as a string, not a number.
    row = pd.DataFrame([{
        "RTE": 1, "DISTRICT": 5, "CNTY": "SB", "PM": "0.001", "PM_PFX": "R",
    }])
    out = _attach_route_type(
        row, route1_district5_sb_shn, pm_col="PM", pm_pfx_col="PM_PFX",
        route_col="RTE", district_col="DISTRICT", cnty_col="CNTY",
    )
    assert out.iloc[0]["route_type_matched"]
    assert out.iloc[0]["RouteType"] == "State"


# ---------------------------------------------------- unit: rank/percentile --

def test_shn_fields_covers_every_column_attach_route_type_needs():
    # Direct regression test for the "County" KeyError found on Suz's live
    # run (2026-09-09): SHN_FIELDS controls the live query's outFields, so
    # anything _attach_route_type reads off `shn` must be requested here,
    # or it works against a full local CSV (which has every column
    # regardless) and then breaks against the real, field-limited query.
    from clean_traffic_volume import SHN_FIELDS

    required = {"District", "County", "Route", "PMPrefix", "bPM", "ePM", "AlignCode", "RouteType"}
    missing = required - set(SHN_FIELDS)
    assert not missing, f"SHN_FIELDS is missing columns _attach_route_type needs: {missing}"


def test_rank_and_percentile_on_known_values():
    df = pd.DataFrame({
        "DISTRICT": [1, 1, 1, 2, 2],
        "AADT": [100, 300, 200, 50, 50],
    })
    out = _add_rank_and_percentile(df, "AADT", group_col="DISTRICT")

    # District 1: 300 is rank 1, 200 is rank 2, 100 is rank 3
    d1 = out[out["DISTRICT"] == 1].set_index("AADT")
    assert d1.loc[300, "district_rank"] == 1
    assert d1.loc[200, "district_rank"] == 2
    assert d1.loc[100, "district_rank"] == 3

    # District 2: tied values both get rank 1 (method="min")
    d2 = out[out["DISTRICT"] == 2]
    assert (d2["district_rank"] == 1).all()

    # percentile is computed across the WHOLE frame, not per group -
    # the two smallest values (50, 50) should have the lowest percentile
    assert out.loc[out["AADT"] == 50, "percentile"].max() < out.loc[out["AADT"] == 300, "percentile"].iloc[0]


# --------------------------------------------------- full pipeline sanity --

def test_transform_aadt_full_pipeline_is_sane(full_aadt_input, full_shn):
    out = transform_aadt(full_aadt_input, full_shn)

    # dedup, ignoring coordinate jitter (13919 -> 7126), then unpivot with
    # the ~1170-per-side nulls dropped
    assert 12000 < len(out) < 14000

    assert out["route_type_matched"].mean() > 0.99
    assert (out["AADT"] > 0).all()
    assert out["AADT"].max() < 500_000  # sanity ceiling, not a hard domain fact

    # every district's top rank should start at 1 and be unique-min per tie
    for district, group in out.groupby("DISTRICT"):
        assert group["district_rank"].min() == 1

    # percentile is a valid 0-100 value everywhere it's set
    assert out["percentile"].between(0, 100).all()


def test_transform_truck_full_pipeline_is_sane(full_truck_input, full_shn):
    out = transform_truck(full_truck_input, full_shn)

    # dedup only, ignoring coordinate jitter (6865 -> 3461), no unpivot
    assert len(out) == 3461

    assert out["route_type_matched"].mean() > 0.99
    assert "DISTRICT" in out.columns and "DIST" not in out.columns
    assert (out["VEHICLE_AADT_TOTAL"] > 0).all()
    assert (out["TOT_TRK_AADT"] >= 0).all()

    # truck volume should never exceed the total vehicle volume at the same point
    assert (out["TOT_TRK_AADT"] <= out["VEHICLE_AADT_TOTAL"]).all()


# ----------------------------------------------------- unit: live fetch --
# These mock requests.get with a real AADT feature's actual shape (an
# attributes dict plus a WGS84 point geometry {"x": lon, "y": lat}, which is
# what an ArcGIS point-layer query with outSR=4326 returns) to exercise the
# pagination and geometry-flattening code paths that the full-pipeline
# tests above can't reach - this sandbox can't hit the live FeatureServer
# directly (see module docstring), so this is the closest thing to an
# end-to-end test of fetch_aadt() itself.

REAL_AADT_FEATURE = {
    # Real Las Cruces record (OBJECTID 1, District 5/Route 1/PM 0.001),
    # with the geometry replaced by its actual WGS84 lon/lat (computed by
    # reprojecting the real District 5 X/Y from EPSG:2230, the same value
    # an outSR=4326 query would hand back directly).
    "attributes": {
        "OBJECTID": 1, "DISTRICT": 5, "RTE": 1, "RTE_SFX": None, "CNTY": "SB",
        "PM_PFX": "R", "PM": 0.001, "PM_SFX": None,
        "DESCRIPTION": "LAS CRUCES, JCT. RTE. 101; MOBIL OIL PIER",
        "BACK_PEAK_HOUR": 40, "BACK_PEAK_MADT": 600, "BACK_AADT": 540,
        "AHEAD_PEAK_HOUR": 860, "AHEAD_PEAK_MADT": 8000, "AHEAD_AADT": 7700,
    },
    "geometry": {"x": -120.601, "y": 34.469},
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_features_to_point_df_flattens_point_geometry():
    df = _features_to_point_df([REAL_AADT_FEATURE])
    assert len(df) == 1
    assert df.iloc[0]["longitude"] == pytest.approx(-120.601)
    assert df.iloc[0]["latitude"] == pytest.approx(34.469)
    assert df.iloc[0]["DESCRIPTION"] == "LAS CRUCES, JCT. RTE. 101; MOBIL OIL PIER"


def test_features_to_point_df_handles_missing_geometry():
    feature = {"attributes": REAL_AADT_FEATURE["attributes"], "geometry": None}
    df = _features_to_point_df([feature])
    assert pd.isna(df.iloc[0]["longitude"])
    assert pd.isna(df.iloc[0]["latitude"])


def test_paged_query_follows_resultOffset_across_pages():
    # Page 1 comes back full (PAGE_SIZE features would normally trigger a
    # second request); here we fake a small PAGE_SIZE-sized-looking
    # response by returning exactly one feature per "page" and a shorter
    # final page, since the real pagination rule is just "keep going while
    # the page is full-sized".
    import clean_traffic_volume as mod

    page1 = {"features": [REAL_AADT_FEATURE] * mod.PAGE_SIZE}
    page2 = {"features": [REAL_AADT_FEATURE]}  # shorter than PAGE_SIZE -> stop

    responses = [_FakeResp(page1), _FakeResp(page2)]
    with patch("clean_traffic_volume.requests.get", side_effect=responses) as mock_get:
        features = _paged_query(AADT_URL, {"where": "1=1"})

    assert len(features) == mod.PAGE_SIZE + 1
    assert mock_get.call_count == 2
    # second call should have asked for the next page via resultOffset
    second_call_params = mock_get.call_args_list[1].kwargs["params"]
    assert second_call_params["resultOffset"] == mod.PAGE_SIZE


def test_paged_query_raises_on_arcgis_error_payload():
    error_payload = {"error": {"code": 400, "message": "Invalid where clause"}}
    with patch("clean_traffic_volume.requests.get", return_value=_FakeResp(error_payload)):
        with pytest.raises(RuntimeError, match="Invalid where clause"):
            _paged_query(AADT_URL, {"where": "bogus"})


def test_fetch_aadt_end_to_end_with_mocked_response():
    payload = {"features": [REAL_AADT_FEATURE]}
    with patch("clean_traffic_volume.requests.get", return_value=_FakeResp(payload)):
        df = fetch_aadt()

    assert len(df) == 1
    assert df.iloc[0]["RTE"] == 1
    assert df.iloc[0]["longitude"] == pytest.approx(-120.601)
    # every field the rest of the pipeline expects should have made it through
    for col in AADT_FIELDS:
        assert col in df.columns