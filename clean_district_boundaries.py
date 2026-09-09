"""
clean_district_boundaries.py

Pulls the Caltrans District boundary polygons ("District_Tiger_Lines") from
the public ArcGIS REST FeatureServer and pairs them with a per-district
summary of the four metrics already produced by the other clean_*.py
scripts in this project - so the dashboard can finally show a real
statewide map instead of only bar charts (Bottlenecks is currently the only
panel with an actual map).

Source layer:
  https://caltrans-gis.dot.ca.gov/arcgis/rest/services/CHboundary/District_Tiger_Lines/FeatureServer/0

Two outputs, on purpose kept separate:
  - data/district_boundaries.geojson - just the 12 district polygons plus a
    single "District" property (renamed from the layer's own "DISTRICT" so
    it matches every other CSV in this project). Geometry never changes
    day to day, so this only needs re-running if Caltrans revises district
    lines.
  - data/district_metrics.csv - one row per district, four columns:
      centerline_miles      from data/district_mileage.csv
      avg_aadt              mean of the AADT column in data/aadt_by_direction.csv,
                             grouped by district (a straight sum isn't
                             meaningful across count-station locations, so
                             this is an average traffic count per station,
                             not a statewide total)
      bottleneck_delay_hours sum of Total_Delay__veh_hrs_ in
                             data/bottlenecks_joined.csv per district - note
                             that file only has the TOP 10 bottlenecks per
                             district, so this is "total delay among each
                             district's 10 worst bottlenecks," not every
                             bottleneck statewide
      climate_risk_pct      % of (hazard, segment) observations in
                             data/ccvra_risk_by_district.csv rated "High" or
                             "Medium-High" for that district, out of all
                             hazard observations for that district across
                             all four hazards

  This second output is NOT an independent live pull - it re-reads the CSVs
  the other four clean_*.py scripts already wrote to data/. Run those first
  (or just keep their existing output in data/ - they don't need to be
  fresh) before running build_district_metrics() here.

This sandbox can't run fetch_district_boundaries() itself end-to-end
(requests.get() to caltrans-gis.dot.ca.gov is blocked here - only WebFetch
can reach it), but the real data has been verified: a full geometry pull
(all 12 features, generalized to maxAllowableOffset=0.02 degrees - plenty
of precision for a statewide choropleth) was retrieved via WebFetch on
2026-09-09 and written straight to data/district_boundaries.geojson, and
build_district_metrics() has been run for real against the project's
existing data/*.csv files - see data/district_metrics.csv. Two things
worth a second look once this runs on a normal connection: District 12 has
by far the smallest centerline-mile total (276 mi, vs. 950+ everywhere
else - correct, since D12 is just Orange County, but worth a gut check),
and District 1, 2, and 9 come back with a null bottleneck_delay_hours
because bottlenecks_joined.csv has no rows for those districts at all (not
just "outside the top 10" - genuinely absent from the source bottleneck
layer). The choropleth should render those as "no data," not zero.
"""

from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent / "data"

DISTRICT_BOUNDARIES_URL = (
    "https://caltrans-gis.dot.ca.gov/arcgis/rest/services/"
    "CHboundary/District_Tiger_Lines/FeatureServer/0/query"
)

# Tiers counted as "elevated risk" for the climate_risk_pct summary metric -
# must match RISK_TIER_ORDER's top two tiers in clean_ccvra_risk.py.
ELEVATED_RISK_TIERS = ("Medium-High", "High")


def fetch_district_boundaries(url: str = DISTRICT_BOUNDARIES_URL) -> dict:
    """Pull the 12 district polygons as GeoJSON, with the DISTRICT property
    renamed to "District" so it matches every other CSV in this project.

    Only 12 features total, well under any FeatureServer page cap, so no
    pagination is needed here (unlike the other fetch_*() functions in this
    project).
    """
    params = {
        "where": "1=1",
        "outFields": "DISTRICT",
        "returnGeometry": "true",
        "f": "geojson",
    }
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    geojson = resp.json()

    if "features" not in geojson:
        raise RuntimeError(f"Unexpected response (no 'features' key): {geojson}")

    for feature in geojson["features"]:
        props = feature.get("properties", {})
        if "DISTRICT" in props:
            props["District"] = props.pop("DISTRICT")

    return geojson


def build_district_metrics(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Merge the four existing per-district CSVs into one metrics table.

    Reads data_dir/district_mileage.csv, aadt_by_direction.csv,
    bottlenecks_joined.csv, and ccvra_risk_by_district.csv - all four must
    already exist (run the other clean_*.py scripts first, or just keep
    whatever's already in data/).
    """
    mileage = pd.read_csv(data_dir / "district_mileage.csv")[["District", "centerline_miles"]]

    aadt = pd.read_csv(data_dir / "aadt_by_direction.csv")
    avg_aadt = (
        aadt.groupby("DISTRICT", as_index=False)["AADT"]
        .mean()
        .rename(columns={"DISTRICT": "District", "AADT": "avg_aadt"})
    )
    avg_aadt["avg_aadt"] = avg_aadt["avg_aadt"].round(0)

    bottlenecks = pd.read_csv(data_dir / "bottlenecks_joined.csv")
    delay = (
        bottlenecks.groupby("District", as_index=False)["Total_Delay__veh_hrs_"]
        .sum()
        .rename(columns={"Total_Delay__veh_hrs_": "bottleneck_delay_hours"})
    )
    delay["bottleneck_delay_hours"] = delay["bottleneck_delay_hours"].round(0)

    ccvra = pd.read_csv(data_dir / "ccvra_risk_by_district.csv")
    ccvra_totals = ccvra.groupby("District")["segment_count"].sum().rename("total")
    ccvra_elevated = (
        ccvra[ccvra["current_risk"].isin(ELEVATED_RISK_TIERS)]
        .groupby("District")["segment_count"]
        .sum()
        .rename("elevated")
    )
    risk_pct = pd.concat([ccvra_totals, ccvra_elevated], axis=1).fillna(0)
    risk_pct["climate_risk_pct"] = (100 * risk_pct["elevated"] / risk_pct["total"]).round(2)
    risk_pct = risk_pct.reset_index()[["District", "climate_risk_pct"]]

    merged = mileage.merge(avg_aadt, on="District", how="outer")
    merged = merged.merge(delay, on="District", how="outer")
    merged = merged.merge(risk_pct, on="District", how="outer")
    merged["District"] = merged["District"].astype(int)
    return merged.sort_values("District").reset_index(drop=True)


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    print(f"Fetching district boundaries from {DISTRICT_BOUNDARIES_URL} ...")
    geojson = fetch_district_boundaries()
    n_features = len(geojson["features"])
    print(f"  pulled {n_features} district polygons")

    geo_path = DATA_DIR / "district_boundaries.geojson"
    with open(geo_path, "w") as f:
        import json

        json.dump(geojson, f)
    print(f"Wrote {geo_path}")

    print("\nBuilding district_metrics.csv from existing data/*.csv files ...")
    metrics = build_district_metrics()
    metrics_path = DATA_DIR / "district_metrics.csv"
    metrics.to_csv(metrics_path, index=False)

    print(metrics.to_string(index=False))
    print(f"\nWrote {metrics_path}")


if __name__ == "__main__":
    main()