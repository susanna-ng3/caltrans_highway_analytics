"""
export_static_preview.py

One-off pre-deployment check (not part of the dashboard itself, not a
clean_*.py pipeline script) - builds a single static HTML file with the same
charts app.py renders, from the same data/ CSVs, so Suz can open it directly
in a browser and eyeball colors/layout/real numbers before pushing to GitHub
+ Streamlit Community Cloud. No Streamlit involved, so none of the "missing
ScriptRunContext" bare-mode warnings apply here - this script is meant to be
run with plain `python`, on purpose.

This is a static snapshot of the "All districts" / all-route-types default
view only - it does not reproduce the dashboard's interactive filtering
(that's what actually running `streamlit run app.py` is for). Regenerate by
re-running this script any time app.py's chart logic or data/ changes.

Usage: python export_static_preview.py
Output: dashboard_preview.html (same directory)
"""

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.io as pio

DATA_DIR = Path(__file__).resolve().parent / "data"
OUT_FILE = Path(__file__).resolve().parent / "dashboard_preview.html"

# Mirrors the constants in app.py - kept in sync by hand since this is a
# throwaway preview script, not something imported by app.py.
BLUE_SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SERIES_BLUE = "#2a78d6"
ROUTE_TYPE_COLORS = {"State": "#2a78d6", "US": "#eb6834", "Interstate": "#1baf7a"}
ORANGE_SEQUENTIAL = ["#e9997c", "#da7d5b", "#c8633c", "#b64717", "#a22b00", "#871700", "#690800"]
RISK_TIER_ORDER = ["Negligible", "Very Low", "Low", "Low-Medium", "Medium", "Medium-High", "High"]
RISK_TIER_COLORS = dict(zip(RISK_TIER_ORDER, ORANGE_SEQUENTIAL))
HAZARD_ORDER = ["Wildfire", "Landslide", "Riverine Flood", "Coastal Flood"]


def load_data():
    mileage = pd.read_csv(DATA_DIR / "district_mileage.csv").sort_values("District")

    bottlenecks = pd.read_csv(DATA_DIR / "bottlenecks_joined.csv")
    bottlenecks["District"] = pd.to_numeric(bottlenecks["District"], errors="coerce").astype("Int64")

    aadt = pd.read_csv(DATA_DIR / "aadt_by_direction.csv")
    aadt["DISTRICT"] = pd.to_numeric(aadt["DISTRICT"], errors="coerce").astype("Int64")

    truck = pd.read_csv(DATA_DIR / "truck_aadt.csv")
    truck["DISTRICT"] = pd.to_numeric(truck["DISTRICT"], errors="coerce").astype("Int64")

    ccvra = pd.read_csv(DATA_DIR / "ccvra_risk_by_district.csv")
    ccvra["District"] = pd.to_numeric(ccvra["District"], errors="coerce").astype("Int64")
    ccvra["current_risk"] = pd.Categorical(ccvra["current_risk"], categories=RISK_TIER_ORDER, ordered=True)

    return mileage, bottlenecks, aadt, truck, ccvra


def build_figures(mileage, bottlenecks, aadt, truck, ccvra):
    figs = {}

    # Mileage
    fig = px.bar(
        mileage, x="District", y="centerline_miles",
        labels={"District": "District", "centerline_miles": "Centerline miles"},
        hover_data={"segment_count": True, "centerline_miles": ":.1f"},
    )
    fig.update_traces(marker_color=SERIES_BLUE)
    fig.update_layout(
        xaxis=dict(type="category", title="District"), yaxis=dict(title="Centerline miles"),
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", margin=dict(t=10, b=10), height=360,
    )
    figs["mileage"] = fig

    # Busiest AADT per district (top-1, matching the app's default "All districts" view)
    top1 = aadt[aadt["district_rank"] == 1].copy().sort_values("AADT", ascending=False)
    top1["location_label"] = (
        "RTE " + top1["RTE"].astype(str) + " PM " + top1["PM"].round(2).astype(str) + " (" + top1["direction"] + ")"
    )
    fig = px.bar(
        top1, x="location_label", y="AADT", color="RouteType", color_discrete_map=ROUTE_TYPE_COLORS,
        labels={"location_label": "", "AADT": "AADT", "RouteType": "Route type"},
        hover_data={"DISTRICT": True, "CNTY": True, "DESCRIPTION": True},
    )
    fig.update_layout(
        xaxis=dict(categoryorder="total descending"), yaxis=dict(title="AADT"),
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", margin=dict(t=10, b=10), height=360,
        legend=dict(orientation="h", y=1.15),
    )
    figs["top10"] = fig

    # AADT vs Truck AADT scatter
    correlation = truck[["VEHICLE_AADT_TOTAL", "TOT_TRK_AADT"]].corr().iloc[0, 1]
    fig = px.scatter(
        truck, x="VEHICLE_AADT_TOTAL", y="TOT_TRK_AADT", color="RouteType", color_discrete_map=ROUTE_TYPE_COLORS,
        hover_data={
            "RTE": True, "CNTY": True, "DISTRICT": True, "TRK_PERCENT_TOT": ":.1f",
            "VEHICLE_AADT_TOTAL": ":,.0f", "TOT_TRK_AADT": ":,.0f",
        },
        labels={"VEHICLE_AADT_TOTAL": "Total vehicle AADT", "TOT_TRK_AADT": "Truck AADT", "RouteType": "Route type"},
    )
    fig.update_traces(marker=dict(size=7, opacity=0.75))
    fig.update_layout(
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", margin=dict(t=10, b=10), height=380,
        legend=dict(orientation="h", y=1.15),
    )
    figs["scatter"] = fig
    figs["_correlation"] = correlation

    # Bottleneck severity map
    mappable = bottlenecks.dropna(subset=["latitude", "longitude"])
    fig = px.scatter_map(
        mappable, lat="latitude", lon="longitude", color="Total_Delay__veh_hrs_", size="Total_Delay__veh_hrs_",
        size_max=20, color_continuous_scale=BLUE_SEQUENTIAL, hover_name="Name",
        hover_data={
            "Fwy": True, "District": True, "Rank": True, "Total_Delay__veh_hrs_": ":,.0f",
            "Avg_Extent__Miles_": ":.1f", "latitude": False, "longitude": False,
        },
        labels={"Total_Delay__veh_hrs_": "Total delay (veh-hrs)"},
        zoom=4.6, center={"lat": 37.2, "lon": -119.5}, height=420, map_style="carto-positron",
    )
    fig.update_layout(margin=dict(l=0, r=0, t=0, b=0), coloraxis_colorbar=dict(title="Delay<br>(veh-hrs)"))
    figs["map"] = fig

    # Climate risk overlay - statewide
    risk_counts = ccvra.groupby(["hazard", "current_risk"], observed=False)["segment_count"].sum().reset_index()
    fig = px.bar(
        risk_counts, x="hazard", y="segment_count", color="current_risk",
        category_orders={"hazard": HAZARD_ORDER, "current_risk": RISK_TIER_ORDER},
        color_discrete_map=RISK_TIER_COLORS,
        labels={"hazard": "", "segment_count": "Roadway segments", "current_risk": "Current risk"},
    )
    fig.update_layout(
        plot_bgcolor="#fcfcfb", paper_bgcolor="#fcfcfb", margin=dict(t=10, b=10), height=420,
        legend=dict(orientation="h", y=1.12, traceorder="normal"), bargap=0.3,
    )
    figs["risk"] = fig

    return figs


def render_html(mileage, bottlenecks, aadt, figs) -> str:
    chart_html = {
        name: pio.to_html(fig, full_html=False, include_plotlyjs=(name == "mileage"), config={"displaylogo": False})
        for name, fig in figs.items() if name != "_correlation"
    }

    kpi_miles = f"{mileage['centerline_miles'].sum():,.0f}"
    kpi_aadt = f"{aadt['AADT'].max():,.0f}"
    kpi_bn_count = f"{len(bottlenecks):,}"
    kpi_bn_delay = f"{bottlenecks['Total_Delay__veh_hrs_'].sum():,.0f}"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Caltrans Highway Analytics - static preview</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background: #fcfcfb; color: #0b0b0b; margin: 0; padding: 32px 40px 80px; }}
  h1 {{ font-size: 1.9rem; margin-bottom: 4px; }}
  .cap {{ color: #52514e; font-size: 0.92rem; max-width: 900px; margin-bottom: 24px; }}
  .banner {{ background: #eef4fc; border: 1px solid #b7d3f6; border-radius: 8px; padding: 10px 16px; margin-bottom: 28px; font-size: 0.88rem; color: #184f95; max-width: 900px; }}
  .kpis {{ display: flex; gap: 40px; margin-bottom: 32px; flex-wrap: wrap; }}
  .kpi .label {{ color: #52514e; font-size: 0.8rem; }}
  .kpi .value {{ font-size: 1.6rem; font-weight: 600; }}
  .row {{ display: flex; gap: 32px; margin-bottom: 36px; flex-wrap: wrap; }}
  .panel {{ flex: 1 1 440px; min-width: 0; }}
  .panel h2 {{ font-size: 1.15rem; margin-bottom: 2px; }}
  .panel .cap {{ font-size: 0.85rem; margin-bottom: 8px; }}
  .full {{ margin-bottom: 36px; }}
</style>
</head>
<body>
  <h1>Caltrans Highway Analytics</h1>
  <p class="cap">Highway mileage, traffic volume, congestion bottlenecks, and CCVRA climate risk - static snapshot
     (statewide, all route types) for a quick visual check before deploying. The real dashboard (<code>streamlit run app.py</code>)
     is interactive with District and Route type filters; this file is not.</p>

  <div class="banner">
    Static preview only - open this file directly in a browser (no server needed). The full interactive dashboard
    still needs <code>streamlit run app.py</code>, not plain <code>python app.py</code>.
  </div>

  <div class="kpis">
    <div class="kpi"><div class="label">Statewide centerline miles</div><div class="value">{kpi_miles}</div></div>
    <div class="kpi"><div class="label">Busiest AADT reading</div><div class="value">{kpi_aadt}</div></div>
    <div class="kpi"><div class="label">Bottlenecks shown</div><div class="value">{kpi_bn_count}</div></div>
    <div class="kpi"><div class="label">Bottleneck delay (veh-hrs)</div><div class="value">{kpi_bn_delay}</div></div>
  </div>

  <div class="row">
    <div class="panel">
      <h2>Highway Mileage by District</h2>
      <div class="cap">State Highway Network Lines, Right / Right Independent, summed by eOdometer - bOdometer.</div>
      {chart_html['mileage']}
    </div>
    <div class="panel">
      <h2>Busiest AADT Reading per District</h2>
      <div class="cap">Annual AADT unpivoted to one row per Back/Ahead direction-segment; colored by route type.</div>
      {chart_html['top10']}
    </div>
  </div>

  <div class="row">
    <div class="panel">
      <h2>AADT vs. Truck AADT</h2>
      <div class="cap">Each point is one truck-count location, correlation r = {figs['_correlation']:.2f}.</div>
      {chart_html['scatter']}
    </div>
  </div>

  <div class="full">
    <h2>Bottleneck Severity Map</h2>
    {chart_html['map']}
  </div>

  <div class="full">
    <h2>Climate Risk Overlay (CCVRA)</h2>
    <div class="cap">Present-day risk tier on non-bridge roadway segments - Wildfire, Landslide, Riverine Flood
      (current_risk) and Coastal Flood (0-ft-sea-level-rise scenario as its present-day equivalent). Erosion isn't
      shown - no present-day baseline in the CCVRA data. Orange sequential ramp, kept distinct from the blue used
      for the bottleneck map above so the two magnitude encodings don't read as the same measurement.</div>
    {chart_html['risk']}
  </div>

</body>
</html>
"""


def main():
    mileage, bottlenecks, aadt, truck, ccvra = load_data()
    figs = build_figures(mileage, bottlenecks, aadt, truck, ccvra)
    html = render_html(mileage, bottlenecks, aadt, figs)
    OUT_FILE.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({OUT_FILE.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()