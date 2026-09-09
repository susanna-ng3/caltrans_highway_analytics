"""
clean_weigh_stations.py

Pulls the "Vehicle_Enforcement_Facilities" layer (Commercial Vehicle
Enforcement Facilities - i.e. truck weigh stations) from the public ArcGIS
REST FeatureServer. This is the simplest of the new layers added in this
round: it already ships its own District field AND its own Latitude/
Longitude columns, so there's no county/district crosswalk and no geometry
wrangling needed - unlike clean_managed_lanes.py and
clean_district_boundaries.py.

Source layer:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Vehicle_Enforcement_Facilities/FeatureServer/0

Kept deliberately simple per the design discussion with Suz (2026-09-09):
station-level rows only (facility name, route, direction, county, postmile,
district, lat/long) so the dashboard can show a per-district count and a
point map straight from Latitude/Longitude. No cross-referencing against
Truck AADT corridors for a "coverage" metric - that's a reasonable follow-up
but a real spatial join, called out as a later phase rather than bundled in
here.

Confirmed by hand via WebFetch on 2026-09-09 (this sandbox can't reach
caltrans-gis.dot.ca.gov with a plain requests.get(), same as every other
script in this project): 53 point features, fields FACILITY_NAME (string),
ROUTE (integer), DIRECTION (string), LOCATION (string), COUNTY (string),
POST_MILE (string), DISTRICT (integer), Latitude/Longitude (double).
Stations span 11 of the 12 districts in the sample pulled (District 5 had
none) - not necessarily true of the full pull, worth confirming for real.

This sandbox can't run fetch_weigh_stations() end-to-end (requests.get() to
caltrans-gis.dot.ca.gov is blocked here), but the real data has been
verified: a full pull (53/53 rows, matching the layer's reported count) was
retrieved via WebFetch on 2026-09-09 and run through transform() - no rows
dropped, no null coordinates. Confirmed stations span 11 of 12 districts
(none in District 5) - result is in data/weigh_stations.csv. Still worth
Suz running main() for real once to confirm the live requests.get() path
matches.
"""

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"

WEIGH_STATIONS_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/Vehicle_Enforcement_Facilities/FeatureServer/0/query"
)

OUT_FIELDS = [
    "OBJECTID",
    "FACILITY_NAME",
    "ROUTE",
    "DIRECTION",
    "LOCATION",
    "COUNTY",
    "POST_MILE",
    "DISTRICT",
    "Latitude",
    "Longitude",
]

PAGE_SIZE = 1000  # layer has ~53 rows total, well under this


def fetch_weigh_stations(url: str = WEIGH_STATIONS_URL) -> pd.DataFrame:
    """Pull every weigh station record from the live FeatureServer.

    Single page in practice (53 rows), but paginated the same way as every
    other fetch_*() in this project for consistency / future-proofing.
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
        raise RuntimeError("No features returned from Vehicle Enforcement Facilities query - check the URL/params.")
    return pd.DataFrame(all_rows)


def transform(raw: pd.DataFrame) -> pd.DataFrame:
    """Light cleaning only: type-cast District/Route/lat/long, drop rows with no coordinates.

    `raw` must have at least: FACILITY_NAME, DISTRICT, Latitude, Longitude.
    """
    df = raw.copy()

    required = {"FACILITY_NAME", "DISTRICT", "Latitude", "Longitude"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    df["DISTRICT"] = pd.to_numeric(df["DISTRICT"], errors="coerce")
    df["Latitude"] = pd.to_numeric(df["Latitude"], errors="coerce")
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors="coerce")

    n_before = len(df)
    df = df.dropna(subset=["DISTRICT", "Latitude", "Longitude"])
    n_dropped = n_before - len(df)
    if n_dropped:
        print(f"  dropped {n_dropped} row(s) missing District or coordinates")

    df["DISTRICT"] = df["DISTRICT"].astype(int)
    df = df.rename(columns={"DISTRICT": "District"})
    return df.sort_values(["District", "FACILITY_NAME"]).reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Fetching weigh stations from {WEIGH_STATIONS_URL} ...")
    raw = fetch_weigh_stations()
    print(f"  pulled {len(raw)} stations")

    clean = transform(raw)

    out_path = DATA_DIR / "weigh_stations.csv"
    clean.to_csv(out_path, index=False)

    counts = clean.groupby("District").size()
    print("\nStations by district:")
    print(counts.to_string())
    print(f"\nWrote {out_path} ({len(clean)} rows)")


if __name__ == "__main__":
    main()