"""
Regression test for clean_shn_lines.transform(), run against a real,
complete download of the SHN Lines layer (not synthetic data).

The reference file `data/SHN_Lines_raw_sample.csv` is a CSV export of the
full layer downloaded from the ArcGIS Open Data portal on 2026-09-08
(5,266 segments). The expected numbers below were computed by hand from
that same file before this test existed, so this is checking the code
against an independently-verified answer, not against itself.

Run with: pytest test_clean_shn_lines.py -v
"""

from pathlib import Path

import pandas as pd
import pytest

from clean_shn_lines import KEEP_ALIGN_CODES, transform

DATA_DIR = Path(__file__).resolve().parent / "data"
REFERENCE_CSV = DATA_DIR / "SHN_Lines_raw_sample.csv"

EXPECTED_TOTAL_SEGMENTS = 5266
EXPECTED_FILTERED_SEGMENTS = 2632  # Right + Right Independent only
EXPECTED_STATEWIDE_MILES = 15068.02

# District: (segment_count, centerline_miles)
EXPECTED_BY_DISTRICT = {
    1: (None, 948.51),
    2: (None, 1729.20),
    3: (None, 1487.88),
    4: (None, 1407.12),
    5: (None, 1151.21),
    6: (None, 1796.23),
    7: (None, 1113.60),
    8: (None, 1821.60),
    9: (None, 962.13),
    10: (None, 1328.99),
    11: (None, 1045.57),
    12: (None, 275.98),
}


@pytest.fixture(scope="module")
def raw():
    if not REFERENCE_CSV.exists():
        pytest.skip(f"Reference file not found at {REFERENCE_CSV}")
    return pd.read_csv(REFERENCE_CSV)


def test_reference_file_row_count(raw):
    assert len(raw) == EXPECTED_TOTAL_SEGMENTS


def test_alignment_codes_seen_are_the_four_expected_values(raw):
    assert set(raw["AlignCode"].unique()) == {
        "Right",
        "Left",
        "Right Independent",
        "Left Independent",
    }


def test_no_negative_or_zero_length_segments(raw):
    odo_len = raw["eOdometer"] - raw["bOdometer"]
    assert (odo_len > 0).all(), "found a segment with zero/negative odometer length"


def test_odometer_and_postmile_lengths_agree(raw):
    # Sanity check referenced in the module docstring: in this dataset,
    # bOdometer/eOdometer and bPM/ePM should give the same segment lengths.
    odo_len = raw["eOdometer"] - raw["bOdometer"]
    pm_len = raw["ePM"] - raw["bPM"]
    assert (odo_len - pm_len).abs().max() < 1e-6


def test_transform_filters_to_right_side_only(raw):
    summary = transform(raw)
    assert summary["segment_count"].sum() == EXPECTED_FILTERED_SEGMENTS


def test_transform_statewide_total(raw):
    summary = transform(raw)
    assert summary["centerline_miles"].sum() == pytest.approx(EXPECTED_STATEWIDE_MILES, abs=0.5)


def test_transform_per_district_totals(raw):
    summary = transform(raw).set_index("District")
    for district, (_, expected_miles) in EXPECTED_BY_DISTRICT.items():
        actual = summary.loc[district, "centerline_miles"]
        assert actual == pytest.approx(expected_miles, abs=0.5), f"District {district} mismatch"


def test_transform_covers_all_twelve_districts(raw):
    summary = transform(raw)
    assert sorted(summary["District"].tolist()) == list(range(1, 13))


def test_transform_raises_on_missing_columns():
    bad = pd.DataFrame({"District": [1], "AlignCode": ["Right"]})
    with pytest.raises(ValueError):
        transform(bad)


def test_keep_align_codes_matches_documented_logic():
    assert KEEP_ALIGN_CODES == ("Right", "Right Independent")