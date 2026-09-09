"""
Tests for clean_managed_lanes.py.

Mocked-request tests only (this sandbox can't reach caltrans-gis.dot.ca.gov
with a plain requests.get() - same limitation as every other clean_*.py
script here). Real-data checks happen once Suz runs main() for real and
pastes back the lane-miles-by-district table, especially the
cross-district-mismatch count transform_hov() prints.

Run with: pytest test_clean_managed_lanes.py -v
"""

from unittest.mock import patch

import pandas as pd
import pytest

import clean_managed_lanes as mod


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

def test_fetch_all_paginates():
    page1 = {"features": [{"attributes": {"OBJECTID": i}} for i in range(mod.PAGE_SIZE)]}
    page2 = {"features": [{"attributes": {"OBJECTID": mod.PAGE_SIZE}}]}
    with patch.object(mod.requests, "get", side_effect=[_FakeResp(page1), _FakeResp(page2)]) as mock_get:
        df = mod._fetch_all("http://fake-url")
    assert len(df) == mod.PAGE_SIZE + 1
    assert mock_get.call_count == 2


def test_fetch_all_raises_on_error():
    with patch.object(mod.requests, "get", return_value=_FakeResp({"error": {"message": "boom"}})):
        with pytest.raises(RuntimeError, match="ArcGIS query error"):
            mod._fetch_all("http://fake-url")


# --------------------------------------------------------- crosswalk --

def test_crosswalk_builds_from_shn_lines():
    fake_shn = pd.DataFrame({
        "County": ["SAC", "SAC", "ED", "PLA"],
        "District": [3, 3, 3, 3],
    })
    with patch.object(mod, "fetch_shn_lines", return_value=fake_shn):
        crosswalk = mod.build_county_district_crosswalk()
    assert set(crosswalk["County"]) == {"SAC", "ED", "PLA"}
    assert (crosswalk["District"] == 3).all()


def test_crosswalk_resolves_ambiguous_county_by_majority(capsys):
    # Real case confirmed 2026-09-09: Kern (KER) has more segments in
    # District 6 than District 9 in this fixture - crosswalk should pick 6,
    # not raise, and should print a note about it.
    fake_shn = pd.DataFrame({
        "County": ["KER", "KER", "KER", "SAC"],
        "District": [6, 6, 9, 3],
    })
    with patch.object(mod, "fetch_shn_lines", return_value=fake_shn):
        crosswalk = mod.build_county_district_crosswalk()
    ker_district = crosswalk.loc[crosswalk["County"] == "KER", "District"].iloc[0]
    assert ker_district == 6
    assert "spans multiple districts" in capsys.readouterr().out


# ------------------------------------------------------------ transform --

CROSSWALK = pd.DataFrame({
    "County": ["SAC", "ED", "PLA"],
    "District": [3, 3, 3],
})


def test_find_lane_miles_column_matches_mangled_names():
    df = pd.DataFrame(columns=["Route", "Length__________Lane_Miles_", "Comments"])
    assert mod._find_lane_miles_column(df) == "Length__________Lane_Miles_"

    df2 = pd.DataFrame(columns=["Route", "Length__Lane_Miles_", "Comments"])
    assert mod._find_lane_miles_column(df2) == "Length__Lane_Miles_"


def test_find_lane_miles_column_raises_if_ambiguous_or_missing():
    with pytest.raises(ValueError):
        mod._find_lane_miles_column(pd.DataFrame(columns=["Route", "Comments"]))


def test_transform_hov_sums_by_district():
    raw = pd.DataFrame({
        "Begin_County": ["SAC", "SAC", "ED"],
        "End_County": ["SAC", "SAC", "ED"],
        "Length__________Lane_Miles_": ["12.691", "12.954", "23.627"],
    })
    out = mod.transform_hov(raw, CROSSWALK)
    assert out["lane_type"].unique().tolist() == ["HOV"]
    total = out.loc[out["District"] == 3, "lane_miles"].iloc[0]
    assert total == pytest.approx(12.691 + 12.954 + 23.627)


def test_transform_hov_drops_unparseable_lane_miles():
    raw = pd.DataFrame({
        "Begin_County": ["SAC", "SAC"],
        "End_County": ["SAC", "SAC"],
        "Length__________Lane_Miles_": ["12.691", "TBD"],
    })
    out = mod.transform_hov(raw, CROSSWALK)
    assert out.loc[out["District"] == 3, "lane_miles"].iloc[0] == pytest.approx(12.691)


def test_transform_hov_handles_cross_district_span_via_begin_county():
    raw = pd.DataFrame({
        "Begin_County": ["SAC"],
        "End_County": ["PLA_UNKNOWN"],  # not in crosswalk -> only begin_district resolves
        "Length__________Lane_Miles_": ["21.546"],
    })
    out = mod.transform_hov(raw, CROSSWALK)
    assert len(out) == 1
    assert out["District"].iloc[0] == 3


def test_transform_hov_drops_rows_with_unmatched_county():
    raw = pd.DataFrame({
        "Begin_County": ["NOT_A_REAL_COUNTY"],
        "End_County": ["NOT_A_REAL_COUNTY"],
        "Length__________Lane_Miles_": ["5.0"],
    })
    out = mod.transform_hov(raw, CROSSWALK)
    assert out.empty


def test_transform_express_lanes_sums_by_district():
    raw = pd.DataFrame({
        "District": [4, 4, 7],
        "Length__Lane_Miles_": [1.2, 1.85, 3.0],
    })
    out = mod.transform_express_lanes(raw)
    assert out["lane_type"].unique().tolist() == ["Express Lane"]
    d4_total = out.loc[out["District"] == 4, "lane_miles"].iloc[0]
    assert d4_total == pytest.approx(3.05)


def test_combine_produces_long_format():
    hov = pd.DataFrame({"District": [3], "lane_miles": [49.27], "lane_type": ["HOV"]})
    express = pd.DataFrame({"District": [4], "lane_miles": [3.05], "lane_type": ["Express Lane"]})
    out = mod.combine(hov, express)
    assert list(out.columns) == ["District", "lane_type", "lane_miles"]
    assert len(out) == 2
    assert set(out["lane_type"]) == {"HOV", "Express Lane"}


# --------------------------------------------------- real-data fixture --

DATA_DIR = mod.DATA_DIR


@pytest.mark.skipif(not (DATA_DIR / "managed_lanes_by_district.csv").exists(), reason="real-data fixture not present")
def test_real_output_has_only_known_lane_types():
    df = pd.read_csv(DATA_DIR / "managed_lanes_by_district.csv")
    assert set(df["lane_type"]) <= {"HOV", "Express Lane"}
    assert (df["lane_miles"] > 0).all()