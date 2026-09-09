"""
clean_bottlenecks.py

Pulls the Caltrans "Bottlenecks" layer (top-10 ranked congestion locations
per district) and spatially joins each one against "State Highway Network
Lines" to pull route context (RouteType, AlignCode, District cross-check)
onto the bottleneck record.

Source layers:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/Bottlenecks/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHhighway/SHN_Lines/FeatureServer/0

Join design (agreed with Suz, 2026-09-08, refined against real coordinates
pulled from both live layers before writing this):
  - Both layers carry real polyline geometry, so we do a genuine geometric
    join (geopandas sjoin_nearest) rather than a plain attribute merge -
    pre-filtered to the same Route so we're never comparing geometry
    across unrelated highways.
  - A first pass against real Route 70 / Yuba County data showed that a
    bottleneck's polyline can sit at distance 0 from *two* different SHN
    Lines segments at once (its congestion extent straddles a segment
    break), and that the Bottlenecks layer's single-letter Direction
    ("E"/"W"/"N"/"S") does NOT reliably correspond to SHN Lines'
    AlignCode/Direction the way you'd expect - compass direction and the
    direction postmiles increase in aren't always the same thing on a
    winding route. So Direction is not used as a hard filter here.
  - Instead, ties (and every match, as a sanity check) are broken using
    postmile-range containment: does the bottleneck's CA_PM fall inside
    the candidate segment's [bPM, ePM]? That resolved the real Route 70
    tie correctly where Direction-matching would have picked the wrong
    segment. If containment doesn't resolve to exactly one candidate
    (e.g. true independent-alignment cases, or a data gap), we fall back
    to whichever candidate is geometrically nearest, and flag the row.
"""

import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import LineString, MultiLineString

DATA_DIR = Path(__file__).resolve().parent / "data"

BOTTLENECKS_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/Bottlenecks/FeatureServer/0/query"
)
SHN_LINES_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHhighway/SHN_Lines/FeatureServer/0/query"
)

BOTTLENECK_FIELDS = [
    "OBJECTID", "District", "Rank", "County", "Fwy", "Name", "Type", "Shift",
    "Direction", "CA_PM", "Beg_PM", "Beg_Cty", "Abs_PM", "Abs_PM_upstream",
    "Number_Days_Active", "Avg_Extent__Miles_", "Total_Delay__veh_hrs_", "Route",
]
SHN_FIELDS = [
    "District", "County", "Route", "PMPrefix", "bPM", "ePM", "PMSuffix",
    "AlignCode", "RouteType", "Direction",
]

PAGE_SIZE = 1000
# Nearest candidates farther than this from a bottleneck are almost
# certainly a data problem (wrong route number, geometry gap), not a
# genuine match - flagged rather than silently accepted.
MAX_MATCH_DISTANCE_M = 500

PM_PATTERN = re.compile(r"^([A-Z]?)(\d+\.?\d*)([A-Z]?)$")


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


def _features_to_gdf(features: list[dict], crs: str) -> gpd.GeoDataFrame:
    """Turn ArcGIS polyline features into a GeoDataFrame.

    A polyline's "paths" list can have more than one part - e.g. a
    bottleneck's congestion extent with a gap in it (a break at an
    interchange), or any segment ArcGIS chose to digitize as multiple
    disconnected pieces. Earlier this only kept single-part geometry and
    silently dropped the rest to None, which took out ~30% of real
    Bottlenecks records (including multi-part ones) from the spatial join
    entirely. Multi-part features become a MultiLineString instead.
    """
    rows, geoms = [], []
    for f in features:
        rows.append(f["attributes"])
        geometry = f.get("geometry")
        paths = geometry["paths"] if geometry else []
        if not paths:
            geoms.append(None)
        elif len(paths) == 1:
            geoms.append(LineString(paths[0]))
        else:
            geoms.append(MultiLineString(paths))
    df = pd.DataFrame(rows)
    return gpd.GeoDataFrame(df, geometry=geoms, crs=crs)


def fetch_bottlenecks() -> gpd.GeoDataFrame:
    """Pull every Bottlenecks record, with geometry, from the live FeatureServer."""
    features = _paged_query(
        BOTTLENECKS_URL,
        {
            "where": "1=1",
            "outFields": ",".join(BOTTLENECK_FIELDS),
            "returnGeometry": "true",
            "f": "json",
            "orderByFields": "OBJECTID",
        },
    )
    if not features:
        raise RuntimeError("No features returned from Bottlenecks query.")
    return _features_to_gdf(features, crs="EPSG:4326")


def fetch_shn_lines_for_routes(routes: list[int]) -> gpd.GeoDataFrame:
    """Pull SHN Lines with geometry, restricted to the given Route numbers.

    Bottlenecks only touches a handful of routes statewide, so there's no
    need to pull all 5,266 SHN Lines segments (with geometry, which is much
    heavier than the attribute-only pull in clean_shn_lines.py) - filtering
    server-side to just the relevant routes keeps this fast.
    """
    route_list = ",".join(str(r) for r in sorted(set(routes)))
    features = _paged_query(
        SHN_LINES_URL,
        {
            "where": f"Route IN ({route_list})",
            "outFields": ",".join(SHN_FIELDS),
            "returnGeometry": "true",
            "f": "json",
            "orderByFields": "OBJECTID",
        },
    )
    if not features:
        raise RuntimeError("No features returned from SHN Lines query - check the route list.")
    return _features_to_gdf(features, crs="EPSG:102100")


def _parse_pm(value) -> tuple[str, float, str] | None:
    """'R9.087' -> ('R', 9.087, ''). Returns None if unparseable/blank."""
    if value is None or str(value).strip() == "":
        return None
    m = PM_PATTERN.match(str(value).strip().upper())
    if not m:
        return None
    prefix, number, suffix = m.groups()
    return prefix, float(number), suffix


def transform(bottlenecks: gpd.GeoDataFrame, shn_lines: gpd.GeoDataFrame) -> pd.DataFrame:
    """Spatially join each bottleneck to its SHN Lines segment for route context.

    See module docstring for why this is a nearest-geometry join broken by
    postmile-range containment rather than a plain attribute merge or a
    Direction-letter match.
    """
    bn = bottlenecks.to_crs(epsg=3310).copy()
    shn = shn_lines.to_crs(epsg=3310).copy()

    # a representative point for the dashboard map - a bottleneck's own
    # extent is a line, not a point, so its centroid is the simplest stable
    # "where to draw the dot" choice. Computed in the projected (meters) CRS
    # rather than directly on lon/lat, since planar centroid math on
    # geographic coordinates distorts distances/areas.
    centroids_wgs84 = gpd.GeoSeries(bn.geometry.centroid, crs="EPSG:3310").to_crs(epsg=4326)
    bn["longitude"] = centroids_wgs84.x.values
    bn["latitude"] = centroids_wgs84.y.values

    bn["Route_num"] = pd.to_numeric(bn["Route"], errors="coerce")
    bn["_ca_pm_parsed"] = bn["CA_PM"].apply(_parse_pm)

    matched_rows = []
    for route, bn_group in bn.groupby("Route_num"):
        shn_candidates = shn[shn["Route"] == route]
        if shn_candidates.empty:
            for _, bn_row in bn_group.iterrows():
                matched_rows.append({
                    **bn_row.drop(labels=["geometry", "_ca_pm_parsed"]).to_dict(),
                    "_match_dist_m": None, "_pm_in_range": False,
                })
            continue

        nearest = gpd.sjoin_nearest(
            bn_group, shn_candidates, how="left", distance_col="_match_dist_m",
            rsuffix="shn",
        )

        for objectid, candidates in nearest.groupby("OBJECTID"):
            bn_row = bn_group.loc[bn_group["OBJECTID"] == objectid].iloc[0]
            pm_parsed = bn_row["_ca_pm_parsed"]

            chosen = None
            if pm_parsed is not None and len(candidates) > 1:
                _, ca_pm_val, _ = pm_parsed
                in_range = candidates[
                    (candidates["bPM"] <= ca_pm_val) & (ca_pm_val <= candidates["ePM"])
                ]
                if len(in_range) == 1:
                    chosen = in_range.iloc[0]

            if chosen is None:
                # either only one candidate, or containment didn't resolve
                # a tie uniquely - fall back to the geometrically nearest
                chosen = candidates.sort_values("_match_dist_m").iloc[0]

            pm_in_range = bool(
                pm_parsed is not None and chosen["bPM"] <= pm_parsed[1] <= chosen["ePM"]
            )

            matched_rows.append({
                **bn_row.drop(labels=["geometry", "_ca_pm_parsed"]).to_dict(),
                "shn_District": chosen.get("District_shn", chosen.get("District")),
                "shn_RouteType": chosen["RouteType"],
                "shn_AlignCode": chosen["AlignCode"],
                "shn_Direction": chosen.get("Direction_shn", chosen.get("Direction")),
                "shn_bPM": chosen["bPM"],
                "shn_ePM": chosen["ePM"],
                "_match_dist_m": chosen["_match_dist_m"],
                "_pm_in_range": pm_in_range,
            })

    result = pd.DataFrame(matched_rows)
    result["district_mismatch"] = pd.to_numeric(result["District"], errors="coerce") != pd.to_numeric(
        result["shn_District"], errors="coerce"
    )
    result["low_confidence_match"] = (
        (~result["_pm_in_range"])
        | (result["_match_dist_m"].isna())
        | (result["_match_dist_m"] > MAX_MATCH_DISTANCE_M)
    )
    result["Total_Delay__veh_hrs_"] = pd.to_numeric(result["Total_Delay__veh_hrs_"], errors="coerce")
    result["Rank"] = pd.to_numeric(result["Rank"], errors="coerce")
    return result.sort_values(["District", "Rank"]).reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Fetching Bottlenecks from {BOTTLENECKS_URL} ...")
    bottlenecks = fetch_bottlenecks()
    print(f"  pulled {len(bottlenecks)} bottleneck locations")

    routes = pd.to_numeric(bottlenecks["Route"], errors="coerce").dropna().astype(int).tolist()
    print(f"Fetching SHN Lines for {len(set(routes))} distinct routes ...")
    shn_lines = fetch_shn_lines_for_routes(routes)
    print(f"  pulled {len(shn_lines)} SHN Lines segments")

    result = transform(bottlenecks, shn_lines)

    n_low_conf = result["low_confidence_match"].sum()
    if n_low_conf:
        print(f"  NOTE: {n_low_conf} of {len(result)} bottlenecks had a low-confidence route match - see 'low_confidence_match' column")

    out_path = DATA_DIR / "bottlenecks_joined.csv"
    result.drop(columns=["_ca_pm_parsed"], errors="ignore").to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()