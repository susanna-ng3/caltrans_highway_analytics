"""
Tests for clean_weigh_stations.py.

Mocked-request tests only, same reason as test_clean_ccvra_risk.py: this
sandbox cannot reach caltrans-gis.dot.ca.gov with a plain requests.get().
Real-data checks happen once Suz runs main() for real and pastes back the
per-district station counts.

Run with: pytest test_clean_weigh_stations.py -v
"""

from unittest.mock import patch

import pandas as pd
import pytest

import clean_weigh_stations as mod


class _FakeResp:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("simulated HTTP error")

    def json(self):
        return self._payload


@pytest.fixture
def sample_features():
    return {
        "features": [
            {"attributes": {
                "OBJECTID": 1, "FACILITY_NAME": "LITTLE RIVER", "ROUTE": 101,
                "DIRECTION": "Southbound", "LOCATION": "8.8 miles N of Hwy 299",
                "COUNTY": "Humboldt", "POST_MILE": "R97.14", "DISTRICT": 1,
                "Latitude": 41.015355, "Longitude": -124.107715,
            }},
            {"attributes": {
                "OBJECTID": 4, "FACILITY_NAME": "DUNSMUIR GRADE", "ROUTE": 5,
                "DIRECTION": "Southbound", "LOCATION": "1.6 miles S of Hwy 89",
                "COUNTY": "Siskiyou", "POST_MILE": "R6.98", "DISTRICT": 2,
                "Latitude": 41.270751, "Longitude": -122.280801,
            }},
            {"attributes": {
                "OBJECTID": 5, "FACILITY_NAME": "COTTONWOOD", "ROUTE": 5,
                "DIRECTION": "Northbound", "LOCATION": "12.5 miles N of Hwy 36",
                "COUNTY": "Tehama", "POST_MILE": "40.46", "DISTRICT": 2,
                "Latitude": 40.352284, "Longitude": -122.281179,
            }},
        ]
    }


# -------------------------------------------------------------- fetch --

def test_fetch_single_page(sample_features):
    with patch.object(mod.requests, "get", return_value=_FakeResp(sample_features)) as mock_get:
        df = mod.fetch_weigh_stations("http://fake-url")
    assert len(df) == 3
    assert list(df["FACILITY_NAME"]) == ["LITTLE RIVER", "DUNSMUIR GRADE", "COTTONWOOD"]
    mock_get.assert_called_once()


def test_fetch_paginates_until_short_page(sample_features):
    full_page = {"features": [sample_features["features"][0]] * mod.PAGE_SIZE}
    short_page = sample_features
    with patch.object(mod.requests, "get", side_effect=[_FakeResp(full_page), _FakeResp(short_page)]) as mock_get:
        df = mod.fetch_weigh_stations("http://fake-url")
    assert len(df) == mod.PAGE_SIZE + 3
    assert mock_get.call_count == 2


def test_fetch_raises_on_arcgis_error():
    with patch.object(mod.requests, "get", return_value=_FakeResp({"error": {"code": 500, "message": "boom"}})):
        with pytest.raises(RuntimeError, match="ArcGIS query error"):
            mod.fetch_weigh_stations("http://fake-url")


def test_fetch_raises_on_empty_result():
    with patch.object(mod.requests, "get", return_value=_FakeResp({"features": []})):
        with pytest.raises(RuntimeError, match="No features returned"):
            mod.fetch_weigh_stations("http://fake-url")


# ----------------------------------------------------------- transform --

def _raw_df(sample_features):
    return pd.DataFrame([f["attributes"] for f in sample_features["features"]])


def test_transform_renames_district_and_sorts(sample_features):
    raw = _raw_df(sample_features)
    out = mod.transform(raw)
    assert "District" in out.columns
    assert "DISTRICT" not in out.columns
    assert list(out["District"]) == [1, 2, 2]
    # sorted by District then FACILITY_NAME within district
    d2_names = out.loc[out["District"] == 2, "FACILITY_NAME"].tolist()
    assert d2_names == sorted(d2_names)


def test_transform_drops_rows_missing_coordinates(sample_features):
    raw = _raw_df(sample_features)
    raw.loc[0, "Latitude"] = None
    out = mod.transform(raw)
    assert len(out) == 2
    assert "LITTLE RIVER" not in out["FACILITY_NAME"].values


def test_transform_drops_rows_missing_district(sample_features):
    raw = _raw_df(sample_features)
    raw.loc[1, "DISTRICT"] = None
    out = mod.transform(raw)
    assert len(out) == 2
    assert "DUNSMUIR GRADE" not in out["FACILITY_NAME"].values


def test_transform_raises_on_missing_required_column(sample_features):
    raw = _raw_df(sample_features).drop(columns=["Latitude"])
    with pytest.raises(ValueError, match="missing required columns"):
        mod.transform(raw)


def test_transform_coerces_types(sample_features):
    raw = _raw_df(sample_features)
    raw["DISTRICT"] = raw["DISTRICT"].astype(str)  # simulate a live-schema surprise
    out = mod.transform(raw)
    assert out["District"].dtype.kind in "iu"


# --------------------------------------------------- real-data fixture --

DATA_DIR = mod.DATA_DIR


@pytest.mark.skipif(not (DATA_DIR / "weigh_stations.csv").exists(), reason="real-data fixture not present")
def test_real_output_has_no_null_coordinates():
    df = pd.read_csv(DATA_DIR / "weigh_stations.csv")
    assert df["Latitude"].notna().all()
    assert df["Longitude"].notna().all()
    assert df["District"].between(1, 12).all()