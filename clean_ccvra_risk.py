"""
clean_ccvra_risk.py

Pulls Caltrans' CCVRA (Climate Change Vulnerability and Risk Assessment)
layers and builds a composite "how many roadway segments fall into each
risk tier, per hazard, per district" table for the Climate Risk Overlay
dashboard section.

Source layers (all share one ~612K-row asset backbone, republished once
per hazard - confirmed by pulling each layer's field schema on 2026-09-09
and finding the same backbone fields - OBJECTID, unique_feature_id, route,
pmrouteid, caltrans_district, begin_county, pm_alignment, asset_type,
pm_bpostmile, pm_epostmile, bridge_name, asset_id, tunnel_id, culvert_ids,
approach_before_id/after_id - across all 5):

  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CCVRA/CCVRA_Wildfire_Risk/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CCVRA/CCVRA_Landslide_Risk/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CCVRA/CCVRA_Riverine_Flood_Risk/FeatureServer/0
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CCVRA/CCVRA_Coastal_Flood_Risk/FeatureServer/0

CCVRA_Erosion_Risk is deliberately NOT included here - see "Why Erosion is
excluded" below.

Design decisions (agreed with Suz, 2026-09-09):

  - Unit of aggregation is District (caltrans_district), to match the
    dashboard's existing shared District filter - not County.

  - Only present-day risk is shown (no r45/r85 mid/late future-scenario
    columns in v1), even though every layer carries them.

  - Why Erosion is excluded: Wildfire, Landslide, and Riverine Flood all
    score risk on the same axis - a "current_risk" field plus r45/r85
    climate-scenario projections. Coastal Flood and Erosion instead score
    risk per sea-level-rise scenario (slr000/025/050/100/200_risk) - a
    different axis entirely (feet of SLR, not an emissions pathway x time
    horizon). Coastal Flood's slr000 (0 ft of SLR) is a clean stand-in for
    "today," so it's used as that hazard's current_risk. Erosion has no
    slr000 scenario at all - its lowest is slr025 (0.25 ft of SLR) - so
    there is no genuine present-day erosion score in the data to show
    next to the other four. Rather than mislabel a future scenario as
    "current," Erosion is left out of this v1 current-risk composite.
    (It can be added later as its own scenario-comparison view once we
    want to show SLR progression rather than a single "today" snapshot.)

  - This does NOT do a row-level join across hazards on unique_feature_id.
    The composite is "count of segments per risk tier, per hazard, per
    district" - each hazard is aggregated independently (by
    District + asset_type + its own current-risk field) and the four
    results are stacked long-form with a `hazard` column. That gives the
    same chart with a fraction of the data pulled (aggregated counts, not
    4 x ~612K raw rows). unique_feature_id remains available as the join
    key on the raw layers if a future feature needs a true per-asset join
    (e.g. "segments at High risk from 2+ hazards").

  - asset_type filtering ("roadway only", agreed with Suz 2026-09-09):
    real values seen include "Bridge", "Not a Bridge", "Within 100ft of a
    Bridge", and "Culvert" - but what those actually partition is not
    fully understood yet (a check of "not Bridge" only returned 157 of
    ~612K rows, which is unexplained). ROADWAY_ASSET_TYPES below is a
    provisional first cut (just "Not a Bridge"). main() writes the full,
    real asset_type x hazard breakdown to
    data/ccvra_asset_type_breakdown.csv BEFORE filtering specifically so
    Suz can check that breakdown against her own knowledge of the data
    and confirm/correct ROADWAY_ASSET_TYPES once this has been run for
    real - do not treat the filtered output as final until she has.

  - current_risk is a 7-tier ordinal (Negligible < Very Low < Low <
    Low-Medium < Medium < Medium-High < High - confirmed no "Very High"
    tier exists). Enforced as a fixed category order (RISK_TIER_ORDER),
    not left to sort alphabetically, per the dataviz convention for
    ordinal categories. Any row whose risk value isn't one of these 7
    (nulls, or a value we haven't seen) is dropped and counted/printed
    rather than silently included as an eighth category.

  - Aggregation strategy: 612K rows x 4 layers is too much to pull raw
    the way clean_traffic_volume.py pulls AADT. Each layer's metadata
    claims supportsStatistics: true, so this tries server-side
    groupByFieldsForStatistics first (returns just the district x
    asset_type x risk_tier counts directly). That query could not be
    verified from this environment - every attempt here returned either
    a 400 error or a suspicious cached-looking response through the
    sandbox's fetch tool, which cannot be told apart from a genuine
    service limitation without hitting the live endpoint directly. If
    the stats query fails for any reason, this automatically falls back
    to paging the full attribute table (District/asset_type/risk field
    only, no geometry) and aggregating with pandas - slower, but correct
    either way. Whichever path actually ran is printed per hazard so we
    know, the first time this runs for real, which one Caltrans' server
    actually supports.
"""

import json
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"

_BASE = "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CCVRA"

# hazard label -> (FeatureServer query URL, name of that layer's "present-day risk" field)
CCVRA_LAYERS = {
    "Wildfire": (f"{_BASE}/CCVRA_Wildfire_Risk/FeatureServer/0/query", "current_risk"),
    "Landslide": (f"{_BASE}/CCVRA_Landslide_Risk/FeatureServer/0/query", "current_risk"),
    "Riverine Flood": (f"{_BASE}/CCVRA_Riverine_Flood_Risk/FeatureServer/0/query", "current_risk"),
    "Coastal Flood": (f"{_BASE}/CCVRA_Coastal_Flood_Risk/FeatureServer/0/query", "slr000_risk"),
}

RISK_TIER_ORDER = [
    "Negligible", "Very Low", "Low", "Low-Medium", "Medium", "Medium-High", "High",
]

# Provisional - see "asset_type filtering" in the module docstring. Confirm
# against data/ccvra_asset_type_breakdown.csv (written by main() below)
# before trusting the filtered output.
ROADWAY_ASSET_TYPES = ("Not a Bridge",)

PAGE_SIZE = 1000


def _paged_query_attrs(url: str, fields: list[str]) -> list[dict]:
    """Page a FeatureServer query via resultOffset, attributes only (no geometry)."""
    features = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": ",".join(fields),
            "returnGeometry": "false",
            "f": "json",
            "orderByFields": "OBJECTID",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
        }
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"ArcGIS query error from {url}: {payload['error']}")
        page = payload.get("features", [])
        features.extend(page)
        if len(page) < PAGE_SIZE:
            return features
        offset += PAGE_SIZE


def _server_side_counts(url: str, group_fields: list[str], count_field: str = "unique_feature_id") -> pd.DataFrame:
    """Try groupByFieldsForStatistics for a count per group, straight from the server.

    Raises RuntimeError on anything that isn't a clean {"features": [...]}
    response, so callers can fall back to client-side aggregation.
    """
    stats = [{"statisticType": "count", "onStatisticField": count_field, "outStatisticFieldName": "n"}]
    params = {
        "where": "1=1",
        "groupByFieldsForStatistics": ",".join(group_fields),
        "outStatistics": json.dumps(stats),
        "f": "json",
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise RuntimeError(f"stats query error from {url}: {payload['error']}")
    features = payload.get("features")
    if not features:
        raise RuntimeError(f"stats query from {url} returned no features (payload: {payload})")
    df = pd.DataFrame([f["attributes"] for f in features])
    if "n" not in df.columns:
        raise RuntimeError(f"stats query from {url} response missing 'n' count column: {df.columns.tolist()}")
    return df.rename(columns={"n": "segment_count"})


def _client_side_counts(url: str, group_fields: list[str], id_field: str = "unique_feature_id") -> pd.DataFrame:
    """Fallback: page the full attribute table and aggregate with pandas."""
    fields = list(dict.fromkeys(group_fields + [id_field]))
    raw = _paged_query_attrs(url, fields)
    if not raw:
        raise RuntimeError(f"No features returned from {url} - check field names/where clause.")
    df = pd.DataFrame([f["attributes"] for f in raw])
    agg = (
        df.groupby(group_fields, dropna=False)[id_field]
        .count()
        .reset_index(name="segment_count")
    )
    return agg


def fetch_hazard_district_risk_counts(hazard: str, url: str, risk_field: str) -> pd.DataFrame:
    """One hazard's segment counts, grouped by District x asset_type x its risk field."""
    group_fields = ["caltrans_district", "asset_type", risk_field]
    try:
        agg = _server_side_counts(url, group_fields)
        method = "server-side stats"
    except Exception as exc:
        print(f"    server-side aggregation failed ({exc}); falling back to full pull + pandas groupby")
        agg = _client_side_counts(url, group_fields)
        method = "client-side aggregation (full pull)"

    agg = agg.rename(columns={"caltrans_district": "District", risk_field: "current_risk"})
    agg["hazard"] = hazard
    print(f"    {hazard}: {agg['segment_count'].sum():,.0f} rows via {method} ({len(agg)} groups)")
    return agg[["hazard", "District", "asset_type", "current_risk", "segment_count"]]


def build_asset_type_breakdown(all_raw: pd.DataFrame) -> pd.DataFrame:
    """Real asset_type x hazard counts, BEFORE the roadway-only filter. For Suz to check."""
    return (
        all_raw.groupby(["hazard", "asset_type"], dropna=False)["segment_count"]
        .sum()
        .reset_index()
        .sort_values(["hazard", "segment_count"], ascending=[True, False])
    )


def transform(all_raw: pd.DataFrame) -> pd.DataFrame:
    """Filter to roadway asset types + valid risk tiers, then re-aggregate over asset_type."""
    df = all_raw.copy()
    df["District"] = pd.to_numeric(df["District"], errors="coerce").astype("Int64")

    before = df["segment_count"].sum()
    df = df[df["asset_type"].isin(ROADWAY_ASSET_TYPES)]
    print(f"  asset_type filter ({ROADWAY_ASSET_TYPES}): {before:,.0f} -> {df['segment_count'].sum():,.0f} rows")

    before = df["segment_count"].sum()
    n_bad_tiers = df.loc[~df["current_risk"].isin(RISK_TIER_ORDER), "segment_count"].sum()
    df = df[df["current_risk"].isin(RISK_TIER_ORDER)]
    if n_bad_tiers:
        print(f"  dropped {n_bad_tiers:,.0f} rows with an unrecognized/null current_risk value")

    out = (
        df.groupby(["hazard", "District", "current_risk"], dropna=False)["segment_count"]
        .sum()
        .reset_index()
    )
    out["current_risk"] = pd.Categorical(out["current_risk"], categories=RISK_TIER_ORDER, ordered=True)
    return out.sort_values(["hazard", "District", "current_risk"]).reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    parts = []
    for hazard, (url, risk_field) in CCVRA_LAYERS.items():
        print(f"Fetching {hazard} ({url}) ...")
        parts.append(fetch_hazard_district_risk_counts(hazard, url, risk_field))
    all_raw = pd.concat(parts, ignore_index=True)

    breakdown = build_asset_type_breakdown(all_raw)
    breakdown_out = DATA_DIR / "ccvra_asset_type_breakdown.csv"
    breakdown.to_csv(breakdown_out, index=False)
    print(f"\nWrote {breakdown_out} - real asset_type x hazard counts, check this against "
          f"ROADWAY_ASSET_TYPES = {ROADWAY_ASSET_TYPES} before trusting the filtered output.")
    print(breakdown.to_string(index=False))

    result = transform(all_raw)
    result_out = DATA_DIR / "ccvra_risk_by_district.csv"
    result.to_csv(result_out, index=False)
    print(f"\nWrote {result_out} ({len(result)} hazard x district x risk-tier rows)")


if __name__ == "__main__":
    main()