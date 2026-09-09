"""
clean_shn_lines.py

Pulls the Caltrans "State Highway Network Lines" layer directly from the
public ArcGIS REST FeatureServer and computes total centerline miles per
Caltrans district.

Source layer:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/SHN_Lines/FeatureServer/0

Aggregation logic (agreed with Suz, 2026-09-08):
  - The layer stores each physical highway segment TWICE - once as AlignCode
    "Right" and once as "Left" - because the underlying LRS linework has a
    line for each direction of travel. On undivided highways Right and Left
    are exact duplicates of the same centerline, so summing both would
    double-count centerline miles.
  - Where a divided highway's carriageways physically diverge (around an
    obstacle, or onto separate one-way streets), Caltrans marks those rows
    "Right Independent" / "Left Independent" instead, since the two sides
    no longer share a length.
  - To get one centerline-mile figure per segment without double-counting,
    we keep only AlignCode in {"Right", "Right Independent"} - i.e. always
    the right-hand side, whether or not it's independent.
  - Segment length is computed from bOdometer/eOdometer (cumulative true
    route distance) rather than bPM/ePM (postmile), since Caltrans' own
    metadata notes postmiles can go stale after a route realignment while
    odometer is maintained to track actual distance. In this dataset the
    two agree to within floating-point noise, but odometer is the more
    defensible choice going forward.

Verified against a real full download of the layer (5,266 segments,
2026-09-08): statewide total comes to ~15,068 centerline miles, in line
with Caltrans' commonly cited ~15,000 centerline-mile SHN figure. See
test_clean_shn_lines.py for the automated check against that same file.
"""

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"

SHN_LINES_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/SHN_Lines/FeatureServer/0/query"
)

# Fields we actually need - keeping this narrow makes each page smaller
# and keeps the output CSV free of geometry-only columns like Shape_Length.
OUT_FIELDS = [
    "OBJECTID",
    "District",
    "County",
    "Route",
    "RouteS",
    "RteSuffix",
    "PMPrefix",
    "bPM",
    "ePM",
    "PMSuffix",
    "bOdometer",
    "eOdometer",
    "AlignCode",
    "RouteType",
    "Direction",
]

# AlignCode values to keep for a centerline-mile total - see module
# docstring for why "Left" and "Left Independent" are dropped.
KEEP_ALIGN_CODES = ("Right", "Right Independent")

PAGE_SIZE = 1000


def fetch_shn_lines(url: str = SHN_LINES_URL) -> pd.DataFrame:
    """Pull every SHN Lines record from the live FeatureServer, paginated.

    ArcGIS FeatureServer query endpoints cap how many records they'll
    return in one call (commonly 1000-2000), so we page through with
    resultOffset until a page comes back with fewer than PAGE_SIZE rows.
    """
    all_rows = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": ",".join(OUT_FIELDS),
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
            "orderByFields": "OBJECTID",
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
        raise RuntimeError("No features returned from SHN Lines query - check the URL/params.")

    return pd.DataFrame(all_rows)


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn raw SHN Lines rows into a per-district centerline-mile summary.

    `raw` must have at least the columns: District, AlignCode, bOdometer,
    eOdometer. Works the same whether `raw` came from the live FeatureServer
    query or from a CSV/GeoJSON export with the same field names.
    """
    df = raw.copy()

    required = {"District", "AlignCode", "bOdometer", "eOdometer"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    filtered = df[df["AlignCode"].isin(KEEP_ALIGN_CODES)].copy()
    filtered["centerline_miles"] = filtered["eOdometer"] - filtered["bOdometer"]

    summary = (
        filtered.groupby("District", as_index=False)
        .agg(
            segment_count=("centerline_miles", "size"),
            centerline_miles=("centerline_miles", "sum"),
        )
        .sort_values("District")
        .reset_index(drop=True)
    )
    summary["centerline_miles"] = summary["centerline_miles"].round(2)
    return summary


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Fetching SHN Lines from {SHN_LINES_URL} ...")
    raw = fetch_shn_lines()
    print(f"  pulled {len(raw)} segments")

    summary = transform(raw)

    out_path = DATA_DIR / "district_mileage.csv"
    summary.to_csv(out_path, index=False)

    print(f"\nCenterline miles by district (statewide total: {summary['centerline_miles'].sum():.2f}):")
    print(summary.to_string(index=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()