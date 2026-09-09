"""
Tests for clean_district_boundaries.py.

fetch_district_boundaries() is tested with a mocked request (same reason as
every other clean_*.py test file: this sandbox can't reach
caltrans-gis.dot.ca.gov directly). build_district_metrics() is tested
against small hand-built CSVs written to a temp data dir, since it doesn't
touch the network at all - it only reads whatever the other four
clean_*.py scripts already wrote to data/.

Run with: pytest test_clean_district_boundaries.py -v
"""

from unittest.mock import patch

import pandas as pd
import pytest

import clean_district_boundaries as mod


class _FakeResp:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("simulated HTTP error")

    def json(self):
        return self._payload


# ------------------------------------------------------------- fetching --

def test_fetch_district_boundaries_renames_property():
    fake_geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"DISTRICT": 4}, "geometry": {"type": "Polygon", "coordinates": []}},
            {"type": "Feature", "properties": {"DISTRICT": 7}, "geometry": {"type": "Polygon", "coordinates": []}},
        ],
    }
    with patch.object(mod.requests, "get", return_value=_FakeResp(fake_geojson)) as mock_get:
        out = mod.fetch_district_boundaries("http://fake-url")
    assert [f["properties"]["District"] for f in out["features"]] == [4, 7]
    assert all("DISTRICT" not in f["properties"] for f in out["features"])
    mock_get.assert_called_once()


def test_fetch_district_boundaries_raises_on_bad_response():
    with patch.object(mod.requests, "get", return_value=_FakeResp({"error": {"message": "boom"}})):
        with pytest.raises(RuntimeError, match="Unexpected response"):
            mod.fetch_district_boundaries("http://fake-url")


# --------------------------------------------------------------- metrics --

@pytest.fixture
def populated_data_dir(tmp_path):
    (tmp_path).mkdir(exist_ok=True)

    pd.DataFrame({
        "District": [3, 4],
        "segment_count": [100, 150],
        "centerline_miles": [1000.0, 800.0],
    }).to_csv(tmp_path / "district_mileage.csv", index=False)

    pd.DataFrame({
        "DISTRICT": [3, 3, 4],
        "AADT": [10000, 20000, 5000],
    }).to_csv(tmp_path / "aadt_by_direction.csv", index=False)

    pd.DataFrame({
        "District": [3, 3, 4],
        "Total_Delay__veh_hrs_": [1000.0, 2000.0, 500.0],
    }).to_csv(tmp_path / "bottlenecks_joined.csv", index=False)

    pd.DataFrame({
        "hazard": ["Wildfire", "Wildfire", "Wildfire", "Wildfire"],
        "District": [3, 3, 4, 4],
        "current_risk": ["High", "Negligible", "Medium-High", "Low"],
        "segment_count": [10, 90, 5, 95],
    }).to_csv(tmp_path / "ccvra_risk_by_district.csv", index=False)

    return tmp_path


def test_build_district_metrics_computes_expected_values(populated_data_dir):
    out = mod.build_district_metrics(populated_data_dir)

    assert list(out["District"]) == [3, 4]

    d3 = out[out["District"] == 3].iloc[0]
    assert d3["centerline_miles"] == 1000.0
    assert d3["avg_aadt"] == pytest.approx(15000.0)
    assert d3["bottleneck_delay_hours"] == pytest.approx(3000.0)
    assert d3["climate_risk_pct"] == pytest.approx(10.0)  # 10 of 100 elevated

    d4 = out[out["District"] == 4].iloc[0]
    assert d4["avg_aadt"] == pytest.approx(5000.0)
    assert d4["climate_risk_pct"] == pytest.approx(5.0)  # 5 of 100 elevated


def test_build_district_metrics_handles_district_missing_from_one_source(populated_data_dir):
    # District 4 has no bottleneck rows at all - outer join should keep it with NaN, not drop it.
    bottlenecks = pd.read_csv(populated_data_dir / "bottlenecks_joined.csv")
    bottlenecks = bottlenecks[bottlenecks["District"] != 4]
    bottlenecks.to_csv(populated_data_dir / "bottlenecks_joined.csv", index=False)

    out = mod.build_district_metrics(populated_data_dir)
    d4 = out[out["District"] == 4].iloc[0]
    assert pd.isna(d4["bottleneck_delay_hours"])


# --------------------------------------------------- real-data fixture --

DATA_DIR = mod.DATA_DIR


@pytest.mark.skipif(not (DATA_DIR / "district_metrics.csv").exists(), reason="real-data fixture not present")
def test_real_metrics_cover_all_12_districts():
    df = pd.read_csv(DATA_DIR / "district_metrics.csv")
    assert sorted(df["District"]) == list(range(1, 13))


@pytest.mark.skipif(not (DATA_DIR / "district_boundaries.geojson").exists(), reason="real-data fixture not present")
def test_real_geojson_has_12_features_with_district_property():
    import json

    geojson = json.loads((DATA_DIR / "district_boundaries.geojson").read_text())
    assert len(geojson["features"]) == 12
    assert all("District" in f["properties"] for f in geojson["features"])