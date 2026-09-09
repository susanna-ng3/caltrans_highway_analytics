"""
clean_managed_lanes.py

Pulls both the "HOV" and "Express_Lanes" layers from the public ArcGIS REST
FeatureServer and combines them into one "managed lanes" summary: lane-miles
per district, split by lane type (HOV vs. Express Lane). Combined on
purpose rather than kept as two separate charts - several Express Lanes
rows literally say "Conversion from HOV to Express Lane" in their Comments
field, so the two are the same underlying network at different points in
time, not unrelated data sources.

Source layers:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/HOV/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Express_Lanes/FeatureServer/0

Key schema difference between the two layers (confirmed by hand via WebFetch
on 2026-09-09, since this sandbox can't reach caltrans-gis.dot.ca.gov with a
plain requests.get() - only WebFetch can):
  - Express_Lanes has a District field directly (31 rows total, 13 of them
    District 4 - the Bay Area toll network, which tracks).
  - HOV has NO District field at all - only Begin_County/End_County (124
    rows total, standard 3-letter Caltrans county codes like SAC/ED/PLA).
    To get lane-miles per district we build a county -> district crosswalk
    from clean_shn_lines.fetch_shn_lines() (which already carries both
    District and County for every SHN segment) instead of hand-typing a
    lookup table, since Caltrans districts are drawn (almost) along county
    lines - "almost" because Kern County is a real, confirmed exception,
    split between District 6 and District 9 (verified by hand via WebFetch
    on 2026-09-09, a distinct District/County query against SHN Lines).
    build_county_district_crosswalk() resolves any such county to whichever
    district has the most SHN segments for it, and prints every county that
    needed the tiebreaker.

Both layers store their mileage field as "...Lane-Miles..." with a name
mangled by the REST service (parentheses/spaces become underscores, and the
exact mangling differs slightly between the two layers), so both fetch
functions pull outFields=* and the transform functions locate the mileage
column by pattern match instead of hardcoding the exact field name.

A HOV segment's Begin_County and End_County can differ when the lane
crosses a county line mid-segment (e.g. Route 80 EB, Sacramento into
Placer County, seen in the real sample pulled 2026-09-09) - transform_hov()
assigns the segment to Begin_County's district and reports how many rows
had a Begin/End county pair mapping to two different districts, so that
count can be sanity-checked against the real run before trusting it.

This sandbox can't run fetch_hov()/fetch_express_lanes() end-to-end
(requests.get() to caltrans-gis.dot.ca.gov is blocked here), but the real
data has been verified: full pulls of both layers (124/124 HOV rows,
31/31 Express Lane rows) and a full distinct District/County crosswalk
from SHN Lines were retrieved via WebFetch on 2026-09-09 and run through
these exact transform functions. Every HOV county code resolved cleanly
(no cross-district mismatches, no unparseable lane-miles values) and the
result is in data/managed_lanes_by_district.csv: statewide HOV totals
~1,473 lane-miles across 8 districts, Express Lanes ~534 lane-miles across
5 districts (4, 7, 8, 11, 12 - all real toll-lane markets), with District 4
(Bay Area) the largest of both. Still worth Suz running main() for real
once, mainly to confirm the live requests.get()/pagination path itself
behaves the same way WebFetch's one-shot pull did.
"""

import re
from pathlib import Path

import pandas as pd
import requests

from clean_shn_lines import fetch_shn_lines

DATA_DIR = Path(__file__).resolve().parent / "data"

HOV_URL = "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/HOV/FeatureServer/0/query"
EXPRESS_LANES_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Express_Lanes/FeatureServer/0/query"
)

PAGE_SIZE = 1000  # both layers are well under this (124 and 31 rows respectively)


def _fetch_all(url: str) -> pd.DataFrame:
    """Pull every record from a small FeatureServer layer (all fields, no geometry)."""
    all_rows = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
        }
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS query error: {payload['error']}")
        features = payload.get("features", [])
        all_rows.extend(f["attributes"] for f in features)
        if len(features) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if not all_rows:
        raise RuntimeError(f"No features returned from {url} - check the URL/params.")
    return pd.DataFrame(all_rows)


def fetch_hov(url: str = HOV_URL) -> pd.DataFrame:
    return _fetch_all(url)


def fetch_express_lanes(url: str = EXPRESS_LANES_URL) -> pd.DataFrame:
    return _fetch_all(url)


def build_county_district_crosswalk() -> pd.DataFrame:
    """One row per County, mapped to its most common District in SHN Lines.

    Reuses clean_shn_lines.fetch_shn_lines() unchanged - no new endpoint,
    just the County/District columns off the same layer clean_shn_lines.py
    already pulls.

    Most counties belong to exactly one district, but Kern County is a real,
    confirmed exception (split between District 6 and District 9 - verified
    by hand via WebFetch on 2026-09-09) and there may be other boundary
    counties like it. Rather than hard-failing on that, each county is
    assigned to whichever district has the most SHN Lines segments for it
    (a reasonable "which district is this county mostly in" tiebreaker),
    and every county where this actually had to break a tie is printed so
    it can be sanity-checked against the real run.
    """
    shn = fetch_shn_lines()
    pairs = shn[["County", "District"]].dropna()

    counts = pairs.groupby(["County", "District"]).size().rename("n").reset_index()
    counts = counts.sort_values(["County", "n"], ascending=[True, False])
    winners = counts.drop_duplicates(subset="County", keep="first")

    ambiguous_counties = counts.groupby("County")["District"].nunique()
    ambiguous_counties = ambiguous_counties[ambiguous_counties > 1].index
    if len(ambiguous_counties):
        for county in ambiguous_counties:
            rows = counts[counts["County"] == county]
            chosen = winners.loc[winners["County"] == county, "District"].iloc[0]
            print(
                f"  [build_county_district_crosswalk] '{county}' spans multiple districts "
                f"({dict(zip(rows['District'], rows['n']))}) - using District {chosen} (most segments)"
            )

    return winners[["County", "District"]].reset_index(drop=True)


def _find_lane_miles_column(df: pd.DataFrame) -> str:
    candidates = [c for c in df.columns if re.search(r"lane", c, re.I) and re.search(r"mile", c, re.I)]
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one lane-miles column, found {candidates}")
    return candidates[0]


def transform_hov(raw: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """HOV rows -> lane-miles per district, using the county->district crosswalk."""
    df = raw.copy()
    length_col = _find_lane_miles_column(df)
    df["lane_miles"] = pd.to_numeric(df[length_col], errors="coerce")

    n_unparseable = df["lane_miles"].isna().sum()
    if n_unparseable:
        print(f"  [transform_hov] dropping {n_unparseable} row(s) with unparseable lane-miles values")
    df = df.dropna(subset=["lane_miles"])

    county_to_district = dict(zip(crosswalk["County"], crosswalk["District"]))
    df["begin_district"] = df["Begin_County"].map(county_to_district)
    df["end_district"] = df["End_County"].map(county_to_district)

    unmatched = df["begin_district"].isna().sum()
    if unmatched:
        print(f"  [transform_hov] {unmatched} row(s) have a Begin_County not found in the crosswalk")

    mismatched = ((df["begin_district"].notna()) & (df["end_district"].notna()) & (df["begin_district"] != df["end_district"])).sum()
    if mismatched:
        print(f"  [transform_hov] {mismatched} row(s) span two districts (Begin_County != End_County district) - assigned to Begin_County's district")

    df = df.dropna(subset=["begin_district"])
    df["District"] = df["begin_district"].astype(int)

    summary = df.groupby("District", as_index=False)["lane_miles"].sum()
    summary["lane_type"] = "HOV"
    return summary


def transform_express_lanes(raw: pd.DataFrame) -> pd.DataFrame:
    """Express Lanes rows -> lane-miles per district (District field is direct)."""
    df = raw.copy()
    length_col = _find_lane_miles_column(df)
    df["lane_miles"] = pd.to_numeric(df[length_col], errors="coerce")

    n_unparseable = df["lane_miles"].isna().sum()
    if n_unparseable:
        print(f"  [transform_express_lanes] dropping {n_unparseable} row(s) with unparseable lane-miles values")
    df = df.dropna(subset=["lane_miles"])
    df["District"] = pd.to_numeric(df["District"], errors="coerce").astype(int)

    summary = df.groupby("District", as_index=False)["lane_miles"].sum()
    summary["lane_type"] = "Express Lane"
    return summary


def combine(hov_summary: pd.DataFrame, express_summary: pd.DataFrame) -> pd.DataFrame:
    """Long-format table: one row per (District, lane_type), for a grouped/stacked bar chart."""
    combined = pd.concat([hov_summary, express_summary], ignore_index=True)
    combined["lane_miles"] = combined["lane_miles"].round(2)
    combined = combined.sort_values(["District", "lane_type"]).reset_index(drop=True)
    return combined[["District", "lane_type", "lane_miles"]]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print("Building county->district crosswalk from live SHN Lines ...")
    crosswalk = build_county_district_crosswalk()
    print(f"  {len(crosswalk)} county/district pairs")

    print(f"\nFetching HOV lanes from {HOV_URL} ...")
    raw_hov = fetch_hov()
    print(f"  pulled {len(raw_hov)} HOV segments")
    hov_summary = transform_hov(raw_hov, crosswalk)

    print(f"\nFetching Express Lanes from {EXPRESS_LANES_URL} ...")
    raw_express = fetch_express_lanes()
    print(f"  pulled {len(raw_express)} Express Lane segments")
    express_summary = transform_express_lanes(raw_express)

    combined = combine(hov_summary, express_summary)

    out_path = DATA_DIR / "managed_lanes_by_district.csv"
    combined.to_csv(out_path, index=False)

    print("\nManaged lane-miles by district:")
    print(combined.to_string(index=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()