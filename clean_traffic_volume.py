"""
clean_traffic_volume.py

Pulls Caltrans "Annual Average Daily Traffic" (AADT) and "Truck Average
Daily Traffic" and builds the data behind the Traffic Volume dashboard tab:
  - a top-10-busiest-locations-per-district table/chart, from Annual AADT
  - an AADT-vs-truck-AADT scatter, from Truck AADT's own counts

Source layers:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Traffic_AADT/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Truck_Volumes_AADT/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/SHN_Lines/FeatureServer/0  (RouteType only)

Design decisions (agreed with Suz, 2026-09-08 - reached after inspecting a
real full pull of both AADT layers, not just the schema):

  - Annual AADT is a POINT layer. Each row is a single postmile location
    carrying a BACK_AADT (volume on the roadway approaching that point) and
    an AHEAD_AADT (volume leaving it). These are two genuinely different
    road segments meeting at the point, not two directions of the same
    segment, so they're unpivoted into one row per direction-segment rather
    than collapsed with max()/mean() - that keeps "busiest segments" honest
    about what a segment actually is here.

  - Both raw layers contain duplicate readings that need to be dropped
    before ranking or plotting anything. The obvious case - every column
    identical, including coordinates - was ~17% of Annual AADT rows and
    ~20% of Truck AADT rows. But a second, larger pattern showed up once
    the top-10 chart was actually rendered: pairs of rows with the same
    Route/postmile/description/AADT but coordinates a few dozen feet apart
    (median ~51 ft, 99.6% within 500 ft, never more than ~0.3 mi, in a real
    full pull) - consistent with a divided highway's two carriageways each
    being digitized as their own point but assigned the same combined
    traffic count. Left alone, that shows up as two identical bars for the
    same real-world location in a "busiest locations" ranking. So the
    duplicate check ignores X/Y (and the longitude/latitude fetched in
    their place) entirely - two rows agreeing on every non-coordinate field
    are the same reading regardless of how many feet apart they were
    digitized. On the full real pull this drops Annual AADT from 13,919 to
    7,126 rows and Truck AADT from 6,865 to 3,461 (agreed with Suz,
    2026-09-09, after she confirmed collapsing the coordinate-jitter pairs
    is correct for a busiest-locations ranking).

  - Truck AADT already carries both VEHICLE_AADT_TOTAL and TOT_TRK_AADT on
    the same row, so the AADT-vs-truck scatter is built directly from that
    one file - no join to Annual AADT needed for it.

  - Coordinates: rather than depend on the raw layers' native X/Y attribute
    columns (confirmed to be California State Plane Zone VI, EPSG:2230, US
    survey feet, by reprojecting a full pull and checking 100% of points
    land inside California's bounding box, with Del Norte Co. and San
    Diego Co. spot checks matching real geography) we simply ask the
    FeatureServer for point geometry in EPSG:4326 (outSR=4326) directly, so
    every row comes back with ready-to-use longitude/latitude and there's
    no reprojection step to get wrong.

  - RouteType (Freeway / Conventional Highway / Expressway) isn't in either
    AADT file. It's pulled from SHN Lines and attached by District + County
    + Route + postmile containment - does this record's postmile fall
    within [bPM, ePM] of a same-District/County/Route SHN Lines segment?
    Route + postmile alone is NOT a unique key - Caltrans postmiles reset
    at county (and sometimes district) lines, so a real check against Route
    1 / PM 0.001 turned up 11 candidate SHN Lines segments in 5 different
    districts all starting at bPM 0.0 before District/County were added to
    the match key, collapsing it to exactly one. Both AADT files carry
    District/County directly, so this is still a plain attribute join, no
    geometry needed. Candidates are restricted to AlignCode in ("Right",
    "Right Independent") first - the same convention clean_shn_lines.py
    uses - to avoid ambiguous duplicate candidates from divided-highway
    carriageways; falls back to any AlignCode if that leaves a route with
    no candidates at all.
"""

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"

AADT_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/Traffic_AADT/FeatureServer/0/query"
)
TRUCK_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/Truck_Volumes_AADT/FeatureServer/0/query"
)
SHN_LINES_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/SHN_Lines/FeatureServer/0/query"
)

AADT_FIELDS = [
    "OBJECTID", "DISTRICT", "RTE", "RTE_SFX", "CNTY", "PM_PFX", "PM", "PM_SFX",
    "DESCRIPTION", "BACK_PEAK_HOUR", "BACK_PEAK_MADT", "BACK_AADT",
    "AHEAD_PEAK_HOUR", "AHEAD_PEAK_MADT", "AHEAD_AADT",
]
TRUCK_FIELDS = [
    "OBJECTID", "RTE", "RTE_SFX", "DIST", "CNTY", "PM_PFX", "POSTMILE", "PM_SFX",
    "LEG", "DESCRIPTION", "VEHICLE_AADT_TOTAL", "TOT_TRK_AADT", "TRK_PERCENT_TOT",
    "EAL", "EST_YEAR", "EST_CODE",
]
SHN_FIELDS = ["District", "County", "Route", "PMPrefix", "bPM", "ePM", "PMSuffix", "AlignCode", "RouteType"]

PAGE_SIZE = 1000
# Same convention as clean_shn_lines.py - Right/Right Independent gives one
# non-duplicated candidate segment per side of a divided highway.
KEEP_ALIGN_CODES = ("Right", "Right Independent")


def _paged_query(url: str, params: dict) -> list[dict]:
    """Shared pagination helper: pages a FeatureServer query via resultOffset."""
    features = []
    offset = 0
    while True:
        page_params = {**params, "resultOffset": offset, "resultRecordCount": PAGE_SIZE}
        resp = requests.get(url, params=page_params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS query error from {url}: {payload['error']}")
        page = payload.get("features", [])
        features.extend(page)
        if len(page) < PAGE_SIZE:
            return features
        offset += PAGE_SIZE


def _features_to_point_df(features: list[dict]) -> pd.DataFrame:
    """ArcGIS point features (queried with outSR=4326) -> flat DataFrame with lon/lat."""
    rows = []
    for f in features:
        row = dict(f["attributes"])
        geom = f.get("geometry") or {}
        row["longitude"] = geom.get("x")
        row["latitude"] = geom.get("y")
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_aadt() -> pd.DataFrame:
    """Pull every Annual AADT record, with WGS84 point geometry, from the live FeatureServer."""
    features = _paged_query(
        AADT_URL,
        {
            "where": "1=1",
            "outFields": ",".join(AADT_FIELDS),
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "json",
            "orderByFields": "OBJECTID",
        },
    )
    if not features:
        raise RuntimeError("No features returned from Annual AADT query.")
    return _features_to_point_df(features)


def fetch_truck_aadt() -> pd.DataFrame:
    """Pull every Truck AADT record, with WGS84 point geometry, from the live FeatureServer."""
    features = _paged_query(
        TRUCK_URL,
        {
            "where": "1=1",
            "outFields": ",".join(TRUCK_FIELDS),
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "json",
            "orderByFields": "OBJECTID",
        },
    )
    if not features:
        raise RuntimeError("No features returned from Truck AADT query.")
    return _features_to_point_df(features)


def fetch_shn_route_types(routes: list[int]) -> pd.DataFrame:
    """Pull SHN Lines attributes (no geometry needed) for the given Route numbers."""
    route_list = ",".join(str(r) for r in sorted(set(routes)))
    features = _paged_query(
        SHN_LINES_URL,
        {
            "where": f"Route IN ({route_list})",
            "outFields": ",".join(SHN_FIELDS),
            "returnGeometry": "false",
            "f": "json",
            "orderByFields": "OBJECTID",
        },
    )
    if not features:
        raise RuntimeError("No features returned from SHN Lines query - check the route list.")
    return pd.DataFrame([f["attributes"] for f in features])


COORDINATE_COLS = ("OBJECTID", "X", "Y", "longitude", "latitude")


def _drop_exact_duplicates(
    df: pd.DataFrame, ignore_cols: tuple[str, ...] = COORDINATE_COLS,
) -> pd.DataFrame:
    """Drop rows identical to another row in every column except id/coordinates.

    See module docstring - two rows that agree on every non-coordinate
    field (Route, postmile, description, AADT, ...) are the same real
    traffic reading, whether they were digitized at the exact same point or
    a divided highway's two carriageways a few dozen feet apart.
    """
    cols = [c for c in df.columns if c not in ignore_cols]
    before = len(df)
    out = df.drop_duplicates(subset=cols, keep="first").reset_index(drop=True)
    dropped = before - len(out)
    if dropped:
        ignored_present = [c for c in ignore_cols if c in df.columns]
        print(f"  dropped {dropped} duplicate rows (identical except {ignored_present})")
    return out


def _unpivot_aadt(aadt: pd.DataFrame) -> pd.DataFrame:
    """One row per direction-reading (Back / Ahead) instead of one row per point.

    See module docstring: BACK_AADT and AHEAD_AADT are two different
    roadway segments meeting at this postmile point, so they become two
    rows rather than being collapsed into one.
    """
    id_cols = [
        "OBJECTID", "DISTRICT", "RTE", "RTE_SFX", "CNTY", "PM_PFX", "PM", "PM_SFX",
        "DESCRIPTION", "longitude", "latitude",
    ]
    parts = []
    for direction, aadt_col, peak_col, madt_col in [
        ("Back", "BACK_AADT", "BACK_PEAK_HOUR", "BACK_PEAK_MADT"),
        ("Ahead", "AHEAD_AADT", "AHEAD_PEAK_HOUR", "AHEAD_PEAK_MADT"),
    ]:
        part = aadt[id_cols + [aadt_col, peak_col, madt_col]].copy()
        part = part.rename(columns={
            aadt_col: "AADT", peak_col: "peak_hour_volume", madt_col: "peak_month_adt",
        })
        part["direction"] = direction
        parts.append(part)
    long_df = pd.concat(parts, ignore_index=True)
    long_df["AADT"] = pd.to_numeric(long_df["AADT"], errors="coerce")
    long_df = long_df.dropna(subset=["AADT"]).reset_index(drop=True)
    return long_df


def _attach_route_type(
    df: pd.DataFrame,
    shn: pd.DataFrame,
    pm_col: str,
    pm_pfx_col: str,
    route_col: str,
    district_col: str,
    cnty_col: str,
) -> pd.DataFrame:
    """Attach RouteType from SHN Lines by District + County + Route + postmile containment.

    Plain attribute join (no geometry needed - both AADT files carry
    District/County/Route/postmile directly). Route + postmile alone is
    NOT a unique key: Caltrans postmiles reset at county (and sometimes
    district) lines, so District + County narrow the candidate segments to
    the right stretch of the route before the [bPM, ePM] containment check
    runs. See module docstring for the real example that motivated this and
    the AlignCode restriction/fallback.

    Route/District/County are normalized (numeric for Route/District,
    stripped+uppercased for County) on both sides before comparing, rather
    than compared as-fetched. On Suz's first live run this join matched
    0 of 12,994 Annual AADT rows while Truck AADT (same SHN data, same
    function) matched 99.9% - Annual AADT and SHN Lines are two separately
    published Caltrans layers, and the most likely explanation is their
    FeatureServer schemas declare District/Route with different field
    types (e.g. text on one, numeric on the other), so `==` compared
    "5" to 5 and silently found zero candidates for every group. Comparing
    on normalized keys makes the join immune to that regardless of which
    side is typed which way.
    """
    shn = shn.copy()
    shn["bPM"] = pd.to_numeric(shn["bPM"], errors="coerce")
    shn["ePM"] = pd.to_numeric(shn["ePM"], errors="coerce")
    shn["_route_key"] = pd.to_numeric(shn["Route"], errors="coerce")
    shn["_district_key"] = pd.to_numeric(shn["District"], errors="coerce")
    shn["_cnty_key"] = shn["County"].astype(str).str.strip().str.upper()
    shn_primary = shn[shn["AlignCode"].isin(KEEP_ALIGN_CODES)]

    df = df.copy()
    df["_route_key"] = pd.to_numeric(df[route_col], errors="coerce")
    df["_district_key"] = pd.to_numeric(df[district_col], errors="coerce")
    df["_cnty_key"] = df[cnty_col].astype(str).str.strip().str.upper()
    # Same live-schema surprise as Route/District/County (see above), found
    # one layer deeper once the group filtering above started working: the
    # live Annual AADT query returns PM as a string, not a number, which
    # crashed the bPM/ePM containment comparison below with "Invalid
    # comparison between dtype=float64 and str". Normalized once here
    # rather than trusted at the point of comparison.
    df["_pm_key"] = pd.to_numeric(df[pm_col], errors="coerce")

    route_type_by_idx = {}
    matched_by_idx = {}

    for (route, district, cnty), group in df.groupby(["_route_key", "_district_key", "_cnty_key"]):
        base_key = (
            (shn["_route_key"] == route)
            & (shn["_district_key"] == district)
            & (shn["_cnty_key"] == cnty)
        )
        candidates_primary = shn_primary[
            (shn_primary["_route_key"] == route)
            & (shn_primary["_district_key"] == district)
            & (shn_primary["_cnty_key"] == cnty)
        ]
        candidates_all = shn[base_key]
        if candidates_all.empty:
            for idx in group.index:
                route_type_by_idx[idx] = None
                matched_by_idx[idx] = False
            continue

        for idx, row in group.iterrows():
            pm = row["_pm_key"]
            if pd.isna(pm):
                route_type_by_idx[idx] = None
                matched_by_idx[idx] = False
                continue
            pfx = row[pm_pfx_col]
            pfx = pfx if pd.notna(pfx) else ""

            # Prefer Right/Right Independent candidates; only fall back to
            # every AlignCode for THIS row's postmile if the preferred set
            # has a real coverage gap there (e.g. an asymmetric independent
            # alignment on the Left side only) - a real example: Route 273
            # in Shasta Co./District 2 has Right coverage [3.812-15.921]
            # and [16.833-20.033], leaving PM 16.801 covered only by a Left
            # Independent segment.
            in_range = candidates_primary[
                (candidates_primary["bPM"] <= pm) & (pm <= candidates_primary["ePM"])
            ]
            if in_range.empty:
                in_range = candidates_all[
                    (candidates_all["bPM"] <= pm) & (pm <= candidates_all["ePM"])
                ]
            if len(in_range) > 1:
                pfx_match = in_range[in_range["PMPrefix"].fillna("") == pfx]
                if len(pfx_match) >= 1:
                    in_range = pfx_match

            if len(in_range) >= 1:
                route_type_by_idx[idx] = in_range.iloc[0]["RouteType"]
                matched_by_idx[idx] = True
            else:
                route_type_by_idx[idx] = None
                matched_by_idx[idx] = False

    df["RouteType"] = df.index.map(route_type_by_idx)
    df["route_type_matched"] = df.index.map(matched_by_idx)
    return df.drop(columns=["_route_key", "_district_key", "_cnty_key", "_pm_key"])


def _add_rank_and_percentile(df: pd.DataFrame, value_col: str, group_col: str) -> pd.DataFrame:
    df = df.copy()
    df["percentile"] = df[value_col].rank(pct=True) * 100
    df["district_rank"] = (
        df.groupby(group_col)[value_col].rank(ascending=False, method="min").astype(int)
    )
    return df


def transform_aadt(aadt_raw: pd.DataFrame, shn: pd.DataFrame) -> pd.DataFrame:
    aadt = _drop_exact_duplicates(aadt_raw)
    long_df = _unpivot_aadt(aadt)
    long_df = _attach_route_type(
        long_df, shn, pm_col="PM", pm_pfx_col="PM_PFX", route_col="RTE",
        district_col="DISTRICT", cnty_col="CNTY",
    )
    long_df = _add_rank_and_percentile(long_df, "AADT", group_col="DISTRICT")
    return long_df.sort_values(["DISTRICT", "district_rank"]).reset_index(drop=True)


def transform_truck(truck_raw: pd.DataFrame, shn: pd.DataFrame) -> pd.DataFrame:
    truck = _drop_exact_duplicates(truck_raw)
    truck = _attach_route_type(
        truck, shn, pm_col="POSTMILE", pm_pfx_col="PM_PFX", route_col="RTE",
        district_col="DIST", cnty_col="CNTY",
    )
    truck = truck.rename(columns={"DIST": "DISTRICT"})
    for col in ("VEHICLE_AADT_TOTAL", "TOT_TRK_AADT", "TRK_PERCENT_TOT"):
        truck[col] = pd.to_numeric(truck[col], errors="coerce")
    return truck.reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Fetching Annual AADT from {AADT_URL} ...")
    aadt_raw = fetch_aadt()
    print(f"  pulled {len(aadt_raw)} AADT points")

    print(f"Fetching Truck AADT from {TRUCK_URL} ...")
    truck_raw = fetch_truck_aadt()
    print(f"  pulled {len(truck_raw)} truck count points")

    routes = (
        pd.to_numeric(pd.concat([aadt_raw["RTE"], truck_raw["RTE"]]), errors="coerce")
        .dropna().astype(int).unique().tolist()
    )
    print(f"Fetching SHN Lines RouteType for {len(routes)} distinct routes ...")
    shn = fetch_shn_route_types(routes)
    print(f"  pulled {len(shn)} SHN Lines segments")

    aadt_long = transform_aadt(aadt_raw, shn)
    n_unmatched_aadt = (~aadt_long["route_type_matched"]).sum()
    print(
        f"  AADT: {len(aadt_long)} direction-segment readings after unpivot; "
        f"{n_unmatched_aadt} with no RouteType match"
    )

    truck = transform_truck(truck_raw, shn)
    n_unmatched_truck = (~truck["route_type_matched"]).sum()
    print(
        f"  Truck AADT: {len(truck)} rows after cleanup; "
        f"{n_unmatched_truck} with no RouteType match"
    )

    aadt_out = DATA_DIR / "aadt_by_direction.csv"
    aadt_long.to_csv(aadt_out, index=False)
    print(f"\nWrote {aadt_out}")

    truck_out = DATA_DIR / "truck_aadt.csv"
    truck.to_csv(truck_out, index=False)
    print(f"Wrote {truck_out}")


if __name__ == "__main__":
    main()