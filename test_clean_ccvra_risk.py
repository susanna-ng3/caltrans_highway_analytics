"""
Tests for clean_ccvra_risk.py.

Unlike test_clean_traffic_volume.py / test_clean_bottlenecks.py, there are
no real-data fixtures here yet - this sandbox cannot reach the live CCVRA
FeatureServers directly (confirmed repeatedly earlier in this project), so
everything below is built from mocked requests.Response objects, shaped to
match the two real response formats confirmed by hand via WebFetch on
2026-09-09: a groupByFieldsForStatistics response's {"features": [{"attributes":
{...}}]} shape, and an ArcGIS {"error": {...}} payload.

Once Suz runs this for real, the plan is the same as every other clean_*.py
script in this project: paste back whatever breaks (or the printed
asset_type breakdown / row counts if it works), and real-data regression
tests get added the same way test_attach_route_type_survives_pm_as_a_string
etc. were added for clean_traffic_volume.py - reproducing the exact failure
against the old code, then confirming the fix.

Run with: pytest test_clean_ccvra_risk.py -v
"""

from unittest.mock import patch

import pandas as pd
import pytest

import clean_ccvra_risk as mod


class _FakeResp:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("simulated HTTP error")

    def json(self):
        return self._payload


# --------------------------------------------------------------- fixtures --

@pytest.fixture
def stats_response_two_groups():
    """A well-formed groupByFieldsForStatistics response, District 4 & 7, mixed risk tiers."""
    return {
        "features": [
            {"attributes": {"caltrans_district": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "n": 120}},
            {"attributes": {"caltrans_district": 4, "asset_type": "Bridge", "current_risk": "High", "n": 8}},
            {"attributes": {"caltrans_district": 7, "asset_type": "Not a Bridge", "current_risk": "Low", "n": 340}},
            {"attributes": {"caltrans_district": 7, "asset_type": "Not a Bridge", "current_risk": None, "n": 5}},
        ]
    }


@pytest.fixture
def raw_features_for_client_side():
    """Attribute-only features as a full pull would return them (for the fallback path)."""
    return [
        {"attributes": {"caltrans_district": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "unique_feature_id": 1}},
        {"attributes": {"caltrans_district": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "unique_feature_id": 2}},
        {"attributes": {"caltrans_district": 4, "asset_type": "Bridge", "current_risk": "High", "unique_feature_id": 3}},
        {"attributes": {"caltrans_district": 7, "asset_type": "Not a Bridge", "current_risk": "Low", "unique_feature_id": 4}},
    ]


# ---------------------------------------------------- _server_side_counts --

def test_server_side_counts_parses_wellformed_response(stats_response_two_groups):
    with patch.object(mod.requests, "get", return_value=_FakeResp(stats_response_two_groups)):
        out = mod._server_side_counts("http://fake/query", ["caltrans_district", "asset_type", "current_risk"])
    assert len(out) == 4
    assert set(out.columns) == {"caltrans_district", "asset_type", "current_risk", "segment_count"}
    assert out.loc[out["current_risk"] == "Medium", "segment_count"].iloc[0] == 120


def test_server_side_counts_raises_on_error_payload():
    with patch.object(mod.requests, "get", return_value=_FakeResp({"error": {"code": 400, "message": "Unable to complete operation."}})):
        with pytest.raises(RuntimeError, match="stats query error"):
            mod._server_side_counts("http://fake/query", ["caltrans_district"])


def test_server_side_counts_raises_on_empty_features():
    # This is the exact shape a stale/cached WebFetch response looked like
    # during manual investigation ({"count": 0}, no "features" key at all) -
    # make sure it's treated as a failure to fall back from, not silently
    # read as "zero rows, aggregation worked."
    with patch.object(mod.requests, "get", return_value=_FakeResp({"count": 0})):
        with pytest.raises(RuntimeError, match="no features"):
            mod._server_side_counts("http://fake/query", ["caltrans_district"])


# ---------------------------------------------------- _client_side_counts --

def test_client_side_counts_aggregates_paged_features(raw_features_for_client_side):
    # First page full (PAGE_SIZE items) would trigger another request; here
    # the page is short, so _paged_query_attrs should stop after one call.
    with patch.object(mod.requests, "get", return_value=_FakeResp({"features": raw_features_for_client_side})):
        out = mod._client_side_counts("http://fake/query", ["caltrans_district", "asset_type", "current_risk"])
    row = out[(out["caltrans_district"] == 4) & (out["asset_type"] == "Not a Bridge")].iloc[0]
    assert row["segment_count"] == 2
    assert row["current_risk"] == "Medium"


def test_client_side_counts_pages_across_resultOffset():
    page1 = [{"attributes": {"caltrans_district": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "unique_feature_id": i}} for i in range(mod.PAGE_SIZE)]
    page2 = [{"attributes": {"caltrans_district": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "unique_feature_id": mod.PAGE_SIZE}}]
    responses = [_FakeResp({"features": page1}), _FakeResp({"features": page2})]

    def fake_get(url, params=None, timeout=None):
        return responses.pop(0)

    with patch.object(mod.requests, "get", side_effect=fake_get):
        out = mod._client_side_counts("http://fake/query", ["caltrans_district", "asset_type", "current_risk"])
    assert out["segment_count"].iloc[0] == mod.PAGE_SIZE + 1


# ------------------------------------------- fetch_hazard_district_risk_counts --

def test_fetch_falls_back_when_stats_query_fails(raw_features_for_client_side):
    def fake_get(url, params=None, timeout=None):
        if "groupByFieldsForStatistics" in (params or {}):
            return _FakeResp({"error": {"code": 400, "message": "Unable to complete operation."}})
        return _FakeResp({"features": raw_features_for_client_side})

    with patch.object(mod.requests, "get", side_effect=fake_get):
        out = mod.fetch_hazard_district_risk_counts("Wildfire", "http://fake/query", "current_risk")
    assert (out["hazard"] == "Wildfire").all()
    assert out["segment_count"].sum() == 4


def test_fetch_renames_risk_field_to_current_risk(stats_response_two_groups):
    # Coastal Flood passes risk_field="slr000_risk" - the caller's raw
    # attributes come back keyed "current_risk" regardless (see fixture,
    # which already uses that name for simplicity) but the real point of
    # this test is that the OUTPUT column is always "current_risk" no
    # matter which source field name was requested, so downstream code
    # (transform(), the dashboard) never needs to know per-hazard field names.
    with patch.object(mod.requests, "get", return_value=_FakeResp(stats_response_two_groups)):
        out = mod.fetch_hazard_district_risk_counts("Coastal Flood", "http://fake/query", "current_risk")
    assert "current_risk" in out.columns
    assert "slr000_risk" not in out.columns


# --------------------------------------------------------------- transform --

@pytest.fixture
def all_raw_sample():
    return pd.DataFrame([
        {"hazard": "Wildfire", "District": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "segment_count": 100},
        {"hazard": "Wildfire", "District": 4, "asset_type": "Bridge", "current_risk": "High", "segment_count": 8},
        {"hazard": "Wildfire", "District": 4, "asset_type": "Not a Bridge", "current_risk": "Medium", "segment_count": 20},
        {"hazard": "Wildfire", "District": 7, "asset_type": "Not a Bridge", "current_risk": None, "segment_count": 5},
        {"hazard": "Wildfire", "District": 7, "asset_type": "Not a Bridge", "current_risk": "Low", "segment_count": 340},
    ])


def test_transform_drops_non_roadway_asset_types(all_raw_sample):
    out = mod.transform(all_raw_sample)
    # the 8-row Bridge group must not survive
    assert out["segment_count"].sum() == 100 + 20 + 340  # excludes Bridge (8) and null-risk (5)


def test_transform_drops_null_or_unrecognized_risk_tiers(all_raw_sample):
    out = mod.transform(all_raw_sample)
    assert out["current_risk"].isna().sum() == 0
    assert set(out["current_risk"].cat.categories) == set(mod.RISK_TIER_ORDER)


def test_transform_reaggregates_over_asset_type(all_raw_sample):
    # Two Wildfire/District 4/"Not a Bridge"/Medium rows (100 + 20) should
    # collapse into one summed row, not stay split.
    out = mod.transform(all_raw_sample)
    row = out[(out["hazard"] == "Wildfire") & (out["District"] == 4) & (out["current_risk"] == "Medium")]
    assert len(row) == 1
    assert row.iloc[0]["segment_count"] == 120


def test_transform_current_risk_is_ordered_categorical(all_raw_sample):
    out = mod.transform(all_raw_sample)
    assert out["current_risk"].dtype.name == "category"
    assert out["current_risk"].cat.ordered is True
    assert list(out["current_risk"].cat.categories) == mod.RISK_TIER_ORDER


# ------------------------------------------------------ asset_type breakdown --

def test_build_asset_type_breakdown_sums_across_district(all_raw_sample):
    out = mod.build_asset_type_breakdown(all_raw_sample)
    row = out[(out["hazard"] == "Wildfire") & (out["asset_type"] == "Not a Bridge")]
    assert row.iloc[0]["segment_count"] == 100 + 20 + 5 + 340  # includes the null-risk row - this is pre-filter


# ------------------------------------------------------------------ config --

def test_erosion_is_not_in_ccvra_layers():
    # Regression guard for the deliberate exclusion agreed with Suz
    # 2026-09-09 (see module docstring "Why Erosion is excluded") - a
    # future edit adding all 5 layers back in without also fixing the
    # missing-baseline problem should fail loudly here.
    assert "Erosion" not in mod.CCVRA_LAYERS
    assert len(mod.CCVRA_LAYERS) == 4


def test_coastal_flood_uses_slr000_as_its_risk_field():
    assert mod.CCVRA_LAYERS["Coastal Flood"][1] == "slr000_risk"


def test_risk_tier_order_has_no_very_high():
    assert "Very High" not in mod.RISK_TIER_ORDER
    assert mod.RISK_TIER_ORDER[-1] == "High"
    assert len(mod.RISK_TIER_ORDER) == 7