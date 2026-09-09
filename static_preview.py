"""
app.py - Caltrans Highway Analytics dashboard

Single-page layout combining eight analyses, each built from a clean_*.py
output in data/:
  - Highway Mileage by District  (clean_shn_lines.py -> district_mileage.csv)
  - Traffic Volume               (clean_traffic_volume.py -> aadt_by_direction.csv, truck_aadt.csv)
  - Congestion Bottlenecks       (clean_bottlenecks.py -> bottlenecks_joined.csv)
  - Climate Risk Overlay (CCVRA) (clean_ccvra_risk.py -> ccvra_risk_by_district.csv)
  - District Overview Map        (clean_district_boundaries.py -> district_boundaries.geojson, district_metrics.csv)
  - Managed Lanes (HOV/Express)  (clean_managed_lanes.py -> managed_lanes_by_district.csv)
  - Weigh Stations               (clean_weigh_stations.py -> weigh_stations.csv)
  - Bridges                      (data/bridges_combined.csv - see note below, NOT a clean_*.py output)

Originally three tabs, one per analysis, while each was being built and
verified independently. Combined onto one scrolling page (agreed with Suz,
2026-09-08) now that all three (then four) are done: tabs hide the other
analyses while you're looking at one, which works against the goal of one
connected dashboard. A shared District filter drives the KPI row and every
section that carries a District column (which now includes Climate Risk,
Managed Lanes, Weigh Stations, and Bridges); a Route type filter (Traffic
Volume and Bottlenecks both carry RouteType from the SHN Lines join - the
others don't) narrows those two sections further. Selecting a specific
district doesn't shrink the Mileage chart, the District Overview Map, or
the Weigh Stations count chart - those three stay statewide comparisons,
with the selected district highlighted/outlined, since that comparison is
the point of each panel. Traffic Volume, Bottlenecks, Climate Risk,
Managed Lanes, Weigh Stations (map only), and Bridges all genuinely filter
down to the selected district instead, since each is already a
per-district breakdown rather than a district-vs-district comparison.

Bridges is an intentional exception to this project's "everything is
pulled live from an ArcGIS FeatureServer, no manual extracts" rule (agreed
with Suz, 2026-09-09): data/bridges_combined.csv is Suz's own pre-cleaned
25,862-row State Highway + Local bridge inventory (State Highway =
maintained by Caltrans, Local = maintained by a city/county but still
State-owned right-of-way or otherwise tracked in this inventory), the same
file her separate, already-deployed bridge_dashboard.py reads from
D:\\Bridges\\Bridges_Combined.csv. That standalone dashboard stays live
alongside this one (Suz's choice) - this section is a second, integrated
view of the same data using the shared District filter, not a
replacement. Its DIST column already uses this project's exact 1-12
district numbering, so no crosswalk is needed here (unlike Managed
Lanes' HOV segments). No condition/sufficiency rating exists in this
source - age and structure type are proxies, not a safety assessment,
exactly as Suz's original dashboard's caption says.
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DATA_DIR = Path(__file__).resolve().parent / "data"

# Sequential blue ramp (light -> dark) - one hue for magnitude, used for the
# bottleneck severity map so it reads as the same system as the bar charts.
BLUE_SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SERIES_BLUE = "#2a78d6"
MUTED_GRAY = "#c3c2b7"

# Fixed categorical order/hues for RouteType (identity, not magnitude) - first
# three slots of the shared categorical theme, assigned once and never cycled,
# so "State" is the same color in every chart that shows it.
ROUTE_TYPE_COLORS = {"State": "#2a78d6", "US": "#eb6834", "Interstate": "#1baf7a"}

# Sequential orange ramp (light -> dark) for CCVRA risk tiers - a second
# magnitude context alongside the bottleneck map's blue, so the two don't
# read as the same measurement. Per the dataviz convention, a second
# simultaneous sequential context takes the next categorical slot's hue
# (orange, slot 2) rather than a color picked freehand. Generated at a fixed
# hue (~41 deg, anchored on ROUTE_TYPE_COLORS["US"]) with lightness swept
# ordinal-safe (light step >= 2:1 contrast on the #fcfcfb chart surface,
# every adjacent step >= 0.06 OKLCH L apart) and validated with the dataviz
# skill's validate_palette.js --ordinal (all checks pass: monotone lightness,
# adjacent-step gaps, light-end contrast, single hue, hue spread 10 deg).
ORANGE_SEQUENTIAL = ["#e9997c", "#da7d5b", "#c8633c", "#b64717", "#a22b00", "#871700", "#690800"]

# Fixed ordinal order for CCVRA current_risk (Negligible -> High, 7 tiers,
# confirmed no "Very High" tier exists) - must match RISK_TIER_ORDER in
# clean_ccvra_risk.py. Mapped onto ORANGE_SEQUENTIAL (light -> dark) so
# "darker = more severe" reads the same way as the bottleneck map's blue
# ramp, without the two magnitude encodings being visually confusable.
RISK_TIER_ORDER = ["Negligible", "Very Low", "Low", "Low-Medium", "Medium", "Medium-High", "High"]
RISK_TIER_COLORS = dict(zip(RISK_TIER_ORDER, ORANGE_SEQUENTIAL))
# Fixed hazard order (not alphabetical - alphabetical puts Coastal Flood
# first, which reads oddly) - must match CCVRA_LAYERS in clean_ccvra_risk.py.
HAZARD_ORDER = ["Wildfire", "Landslide", "Riverine Flood", "Coastal Flood"]

# Two more sequential single-hue ramps (light -> dark) for the District
# Overview Map, generated the same way as ORANGE_SEQUENTIAL: anchored at the
# shared categorical theme's slot-6 green (#008300) and slot-7 violet
# (#4a3aa7), matching ORANGE_SEQUENTIAL's exact L/C progression at each new
# hue, then validated with validate_palette.js --ordinal (all checks pass:
# monotone lightness, adjacent-step gaps, light-end contrast, single hue).
# The map reuses BLUE_SEQUENTIAL and ORANGE_SEQUENTIAL for the two metrics
# that already have an established color elsewhere on this page (Bottleneck
# Delay, Climate Risk) so "blue = delay" and "orange = climate risk" stay
# true everywhere they appear; Centerline Miles and Avg. AADT have no such
# prior meaning, so they get these two new hues instead of reusing blue/
# orange for something different.
GREEN_SEQUENTIAL = ["#88c183", "#69ad64", "#4c9947", "#2b8427", "#007000", "#005a00", "#004300"]
VIOLET_SEQUENTIAL = ["#a7a7f1", "#908ee4", "#7b76d4", "#665ec5", "#5448b1", "#423695", "#312574"]

# Fixed categorical pair for Managed Lanes' lane_type identity (HOV vs.
# Express Lane) - slots 4 and 5 of the same shared theme RouteType already
# uses slots 1-3 of (blue/orange/aqua), continuing the fixed order into
# unused slots rather than reusing blue/orange for a different meaning on
# the same scrolling page. Validated with validate_palette.js (categorical
# mode): CVD separation and normal-vision floor both pass; contrast vs.
# surface WARNs (both colors read light against #fcfcfb), which is why this
# chart keeps its legend and hover labels rather than relying on color alone.
LANE_TYPE_COLORS = {"HOV": "#eda100", "Express Lane": "#e87ba4"}

# Fixed categorical pair for Bridges' Bridge_Type identity (State Highway
# vs. Local) - slots 6 and 7, continuing the same fixed order again.
# Deliberately NOT blue/orange: Suz's original standalone bridge_dashboard.py
# colored Bridge_Type with those exact two hexes, but this page already uses
# blue/orange for RouteType (State/US) - reusing them here for a different
# categorical meaning on the same scrolling page is exactly the "same color,
# different meaning" confusion Suz flagged earlier in this project (agreed
# fix with Suz, 2026-09-09). Slots 6/7's hues (green/violet) are otherwise
# only anchors for the District Overview Map's *sequential* ramps
# (GREEN_SEQUENTIAL, VIOLET_SEQUENTIAL) - a continuous-fill choropleth reads
# differently enough from a solid point-map dot that reusing the hue family
# doesn't collide, the same precedent already set by blue/orange doing double
# duty as both RouteType identity and BLUE_SEQUENTIAL/ORANGE_SEQUENTIAL
# magnitude ramps elsewhere on this page. Validated with validate_palette.js
# (categorical mode): all checks pass, including contrast vs. surface (unlike
# LANE_TYPE_COLORS, these don't need to lean on the legend alone).
BRIDGE_TYPE_COLORS = {"State Highway": "#008300", "Local": "#4a3aa7"}

st.set_page_config(page_title="Caltrans Highway Analytics", layout="wide")


# --------------------------------------------------------------- Load data --

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

with open(DATA_DIR / "district_boundaries.geojson") as _f:
    district_geojson = json.load(_f)

district_metrics = pd.read_csv(DATA_DIR / "district_metrics.csv")
district_metrics["District"] = pd.to_numeric(district_metrics["District"], errors="coerce").astype("Int64")

managed_lanes = pd.read_csv(DATA_DIR / "managed_lanes_by_district.csv")
managed_lanes["District"] = pd.to_numeric(managed_lanes["District"], errors="coerce").astype("Int64")

weigh_stations = pd.read_csv(DATA_DIR / "weigh_stations.csv")
weigh_stations["District"] = pd.to_numeric(weigh_stations["District"], errors="coerce").astype("Int64")

# Bridges: static input (see module docstring) rather than a clean_*.py
# output, so its light cleanup lives here instead of a separate script.
# Mirrors Suz's own bridge_dashboard.py load_data(): one row has no YRBLT
# and is dropped (can't compute age or decade for it); Decade_Built comes
# in as a float column purely because that one NaN forces the dtype, so it
# reverts to int once the row's gone. CURRENT_YEAR is computed from today's
# date rather than her original script's hardcoded 2026, so Avg. Age doesn't
# quietly go stale next year.
bridges = pd.read_csv(DATA_DIR / "bridges_combined.csv")
bridges = bridges.rename(columns={"DIST": "District"})
bridges["District"] = pd.to_numeric(bridges["District"], errors="coerce").astype("Int64")
bridges = bridges.dropna(subset=["YRBLT"])
bridges["Decade_Built"] = bridges["Decade_Built"].astype(int)
CURRENT_YEAR = pd.Timestamp.now().year

ALL_DISTRICTS = sorted(
    set(mileage["District"].dropna())
    | set(bottlenecks["District"].dropna())
    | set(aadt["DISTRICT"].dropna())
    | set(ccvra["District"].dropna())
)
ROUTE_TYPES = sorted(
    set(aadt["RouteType"].dropna()) | set(bottlenecks["shn_RouteType"].dropna())
)


# ------------------------------------------------------------ Header + filters --

st.title("Caltrans Highway Analytics")
st.caption(
    "Highway mileage, traffic volume, and congestion bottlenecks, each pulled "
    "live from Caltrans' public GIS layers and cross-referenced against State "
    "Highway Network Lines for route context."
)

filter_col1, filter_col2, _ = st.columns([1, 2, 3])
with filter_col1:
    selected_district = st.selectbox("District", ["All districts"] + ALL_DISTRICTS)
with filter_col2:
    selected_route_types = st.multiselect(
        "Route type (Traffic Volume & Bottlenecks)", ROUTE_TYPES, default=ROUTE_TYPES,
    )

district_is_filtered = selected_district != "All districts"

# Mileage has no RouteType dimension, so it only ever responds to District.
bn = bottlenecks[bottlenecks["shn_RouteType"].isin(selected_route_types) | bottlenecks["shn_RouteType"].isna()]
aadt_f = aadt[aadt["RouteType"].isin(selected_route_types) | aadt["RouteType"].isna()]
truck_f = truck[truck["RouteType"].isin(selected_route_types) | truck["RouteType"].isna()]

if district_is_filtered:
    bn = bn[bn["District"] == selected_district]
    aadt_f = aadt_f[aadt_f["DISTRICT"] == selected_district]
    truck_f = truck_f[truck_f["DISTRICT"] == selected_district]
    ccvra_f = ccvra[ccvra["District"] == selected_district]
    lanes_f = managed_lanes[managed_lanes["District"] == selected_district]
    stations_f = weigh_stations[weigh_stations["District"] == selected_district]
    bridges_f = bridges[bridges["District"] == selected_district]
else:
    ccvra_f = ccvra
    lanes_f = managed_lanes
    stations_f = weigh_stations
    bridges_f = bridges


# ------------------------------------------------------------------- KPI row --

kpi1, kpi2, kpi3, kpi4 = st.columns(4)

if district_is_filtered:
    district_miles = mileage.loc[mileage["District"] == selected_district, "centerline_miles"]
    kpi1.metric(f"District {selected_district} centerline miles", f"{district_miles.sum():,.0f}")
else:
    kpi1.metric("Statewide centerline miles", f"{mileage['centerline_miles'].sum():,.0f}")

kpi2.metric(
    "Busiest AADT reading" + ("" if not district_is_filtered else f" (Dist. {selected_district})"),
    f"{aadt_f['AADT'].max():,.0f}" if len(aadt_f) else "-",
)
kpi3.metric("Bottlenecks shown", f"{len(bn):,}")
kpi4.metric("Bottleneck delay (veh-hrs)", f"{bn['Total_Delay__veh_hrs_'].sum():,.0f}")


# ---------------------------------------------------- Row 1: Mileage | Top 10 --

row1_left, row1_right = st.columns(2)

with row1_left:
    st.subheader("Highway Mileage by District")
    st.caption(
        "State Highway Network Lines, one alignment per segment (Right / Right "
        "Independent), summed by eOdometer - bOdometer."
    )

    bar_colors = [
        SERIES_BLUE if (not district_is_filtered or d == selected_district) else MUTED_GRAY
        for d in mileage["District"]
    ]
    fig_mileage = px.bar(
        mileage,
        x="District",
        y="centerline_miles",
        labels={"District": "District", "centerline_miles": "Centerline miles"},
        hover_data={"segment_count": True, "centerline_miles": ":.1f"},
    )
    fig_mileage.update_traces(marker_color=bar_colors)
    fig_mileage.update_layout(
        font=dict(color="#1a1a19"),
        xaxis=dict(type="category", title="District"),
        yaxis=dict(title="Centerline miles"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=360,
    )
    st.plotly_chart(fig_mileage, width='stretch', theme=None)

with row1_right:
    top10_title = "Top 10 Busiest AADT Readings" if district_is_filtered else "Busiest AADT Reading per District"
    st.subheader(top10_title)
    st.caption(
        "Annual AADT unpivoted to one row per Back/Ahead direction-segment; "
        "colored by route type."
    )

    top10 = aadt_f[aadt_f["district_rank"] <= 10].copy()
    if not district_is_filtered:
        top10 = top10[top10["district_rank"] == 1]
    if top10.empty:
        st.info("No AADT readings match the current filters.")
    else:
        top10["location_label"] = (
            "RTE " + top10["RTE"].astype(str)
            + " PM " + top10["PM"].round(2).astype(str)
            + " (" + top10["direction"] + ")"
        )
        top10 = top10.sort_values("AADT", ascending=False)

        fig_top10 = px.bar(
            top10,
            x="location_label",
            y="AADT",
            color="RouteType",
            color_discrete_map=ROUTE_TYPE_COLORS,
            category_orders={"RouteType": list(ROUTE_TYPE_COLORS)},
            labels={"location_label": "", "AADT": "AADT", "RouteType": "Route type"},
            hover_data={"DISTRICT": True, "CNTY": True, "DESCRIPTION": True},
        )
        fig_top10.update_layout(
            font=dict(color="#1a1a19"),
            xaxis=dict(categoryorder="total descending"),
            yaxis=dict(title="AADT"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=360,
            legend=dict(orientation="h", y=1.15),
        )
        st.plotly_chart(fig_top10, width='stretch', theme=None)


# -------------------------------------------- Row 2: Scatter | Bottleneck table --

row2_left, row2_right = st.columns(2)

with row2_left:
    st.subheader("AADT vs. Truck AADT")
    if truck_f.empty:
        st.info("No truck-count locations match the current filters.")
    else:
        correlation = truck_f[["VEHICLE_AADT_TOTAL", "TOT_TRK_AADT"]].corr().iloc[0, 1]
        st.caption(
            f"Each point is one truck-count location, correlation r = {correlation:.2f}. "
            "Total vehicle AADT and truck AADT come from the same Truck AADT record - "
            "no join needed."
        )
        fig_scatter = px.scatter(
            truck_f,
            x="VEHICLE_AADT_TOTAL",
            y="TOT_TRK_AADT",
            color="RouteType",
            color_discrete_map=ROUTE_TYPE_COLORS,
            category_orders={"RouteType": list(ROUTE_TYPE_COLORS)},
            hover_data={
                "RTE": True, "CNTY": True, "DISTRICT": True, "TRK_PERCENT_TOT": ":.1f",
                "VEHICLE_AADT_TOTAL": ":,.0f", "TOT_TRK_AADT": ":,.0f",
            },
            labels={"VEHICLE_AADT_TOTAL": "Total vehicle AADT", "TOT_TRK_AADT": "Truck AADT", "RouteType": "Route type"},
        )
        fig_scatter.update_traces(marker=dict(size=7, opacity=0.75))
        fig_scatter.update_layout(
            font=dict(color="#1a1a19"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=380,
            legend=dict(orientation="h", y=1.15),
        )
        st.plotly_chart(fig_scatter, width='stretch', theme=None)

with row2_right:
    st.subheader("Congestion Bottlenecks")
    n_low_confidence = bn["low_confidence_match"].sum() if len(bn) else 0
    if n_low_confidence:
        st.caption(
            f"⚠ {n_low_confidence} of {len(bn)} shown have a low-confidence route match. "
            "Top-10-per-district ranking, spatially joined to SHN Lines for route context."
        )
    else:
        st.caption("Top-10-per-district ranking, spatially joined to SHN Lines for route context.")

    ranked_cols = [
        "District", "Rank", "County", "Fwy", "Name",
        "Total_Delay__veh_hrs_", "shn_RouteType",
    ]
    if bn.empty:
        st.info("No bottlenecks match the current filters.")
    else:
        ranked_table = (
            bn[ranked_cols]
            .sort_values(["District", "Rank"])
            .rename(columns={
                "Total_Delay__veh_hrs_": "Delay (veh-hrs)",
                "shn_RouteType": "Route type",
            })
        )
        st.dataframe(ranked_table, width='stretch', hide_index=True, height=380)


# ---------------------------------------------------- Row 3: Bottleneck map --

st.subheader("Bottleneck Severity Map")
mappable = bn.dropna(subset=["latitude", "longitude"])
if mappable.empty:
    st.info("No bottlenecks with usable coordinates match the current filters.")
else:
    fig_map = px.scatter_map(
        mappable,
        lat="latitude",
        lon="longitude",
        color="Total_Delay__veh_hrs_",
        size="Total_Delay__veh_hrs_",
        size_max=20,
        color_continuous_scale=BLUE_SEQUENTIAL,
        hover_name="Name",
        hover_data={
            "Fwy": True, "District": True, "Rank": True,
            "Total_Delay__veh_hrs_": ":,.0f", "Avg_Extent__Miles_": ":.1f",
            "latitude": False, "longitude": False,
        },
        labels={"Total_Delay__veh_hrs_": "Total delay (veh-hrs)"},
        zoom=4.6,
        center={"lat": 37.2, "lon": -119.5},
        height=420,
        map_style="carto-positron",
    )
    fig_map.update_layout(
        font=dict(color="#1a1a19"),
        margin=dict(l=0, r=0, t=0, b=0),
        coloraxis_colorbar=dict(title="Delay<br>(veh-hrs)"),
    )
    st.plotly_chart(fig_map, width='stretch', theme=None)


# ---------------------------------------------- Row 4: Climate Risk Overlay --

st.subheader("Climate Risk Overlay (CCVRA)")
st.caption(
    "Present-day risk tier on non-bridge roadway segments, for 4 of the 5 CCVRA "
    "hazards. Wildfire, Landslide, and Riverine Flood use the assessment's own "
    "current_risk score; Coastal Flood uses its 0-ft-sea-level-rise scenario as "
    "the present-day equivalent (its risk is scored per SLR scenario, not by "
    "current_risk). Erosion isn't shown - it has no present-day baseline in the "
    "CCVRA data at all (its lowest scenario already assumes 0.25 ft of sea-level "
    "rise), so it can't be shown as \"current\" without mislabeling a future "
    "scenario."
)

if ccvra_f.empty:
    st.info("No CCVRA data matches the current filters.")
else:
    risk_counts = (
        ccvra_f.groupby(["hazard", "current_risk"], observed=False)["segment_count"]
        .sum()
        .reset_index()
    )
    fig_risk = px.bar(
        risk_counts,
        x="hazard",
        y="segment_count",
        color="current_risk",
        category_orders={"hazard": HAZARD_ORDER, "current_risk": RISK_TIER_ORDER},
        color_discrete_map=RISK_TIER_COLORS,
        labels={"hazard": "", "segment_count": "Roadway segments", "current_risk": "Current risk"},
    )
    fig_risk.update_layout(
        font=dict(color="#1a1a19"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=420,
        legend=dict(orientation="h", y=1.12, traceorder="normal"),
        bargap=0.3,
    )
    st.plotly_chart(fig_risk, width='stretch', theme=None)


# --------------------------------------------------- Row 5: District map --

st.subheader("District Overview Map")
st.caption(
    "The four district-level metrics shown elsewhere on this page, mapped onto "
    "Caltrans' 12 district boundaries so they can be compared geographically "
    "instead of only bar-by-bar. Selecting a district above outlines it here "
    "rather than filtering the map, since the point of a map is seeing all 12 "
    "districts at once."
)

METRIC_OPTIONS = {
    "Centerline Miles": ("centerline_miles", GREEN_SEQUENTIAL, "Centerline miles"),
    "Avg. AADT": ("avg_aadt", VIOLET_SEQUENTIAL, "Avg. AADT"),
    "Bottleneck Delay (top 10/district)": ("bottleneck_delay_hours", BLUE_SEQUENTIAL, "Delay (veh-hrs)"),
    "Climate Risk (% High/Med-High)": ("climate_risk_pct", ORANGE_SEQUENTIAL, "% elevated risk"),
}
selected_metric_label = st.selectbox("Color map by", list(METRIC_OPTIONS))
metric_col, metric_ramp, metric_title = METRIC_OPTIONS[selected_metric_label]

if district_metrics[metric_col].isna().any():
    st.caption(
        f"⚠ {int(district_metrics[metric_col].isna().sum())} district(s) have no data for "
        f"\"{selected_metric_label}\" and show unfilled on the map below."
    )

fig_district_map = px.choropleth_map(
    district_metrics,
    geojson=district_geojson,
    locations="District",
    featureidkey="properties.District",
    color=metric_col,
    color_continuous_scale=metric_ramp,
    hover_data={"District": True, metric_col: ":,.1f"},
    labels={metric_col: metric_title},
    map_style="carto-positron",
    zoom=4.6,
    center={"lat": 37.2, "lon": -119.5},
    opacity=0.85,
    height=460,
)

if district_is_filtered:
    _sel_feature = next(
        f for f in district_geojson["features"] if f["properties"]["District"] == selected_district
    )
    _geom = _sel_feature["geometry"]
    _rings = _geom["coordinates"] if _geom["type"] == "Polygon" else [r for poly in _geom["coordinates"] for r in poly]
    _lons, _lats = [], []
    for _ring in _rings:
        for _lon, _lat in _ring:
            _lons.append(_lon)
            _lats.append(_lat)
        _lons.append(None)
        _lats.append(None)
    fig_district_map.add_scattermap(
        lon=_lons, lat=_lats, mode="lines",
        line=dict(width=3, color="#1a1a19"),
        hoverinfo="skip", showlegend=False,
    )

fig_district_map.update_layout(
    font=dict(color="#1a1a19"),
    margin=dict(l=0, r=0, t=0, b=0),
    coloraxis_colorbar=dict(title=metric_title),
)
st.plotly_chart(fig_district_map, width='stretch', theme=None)


# ------------------------------------------- Row 6: Managed Lanes --

st.subheader("Managed Lanes (HOV & Express Lanes)")
st.caption(
    "HOV and Express Lane lane-miles per district, shown together rather than as "
    "two separate charts - many Express Lanes are literally converted HOV lanes "
    "(several rows in the source data say so directly). HOV segments carry only a "
    "county in Caltrans' data, not a district, so those are assigned to a district "
    "via a county-to-district crosswalk built from SHN Lines; Express Lanes carry "
    "District directly."
)

if lanes_f.empty:
    st.info(
        f"No managed lanes recorded in District {selected_district}."
        if district_is_filtered else "No managed lane data available."
    )
else:
    fig_lanes = px.bar(
        lanes_f,
        x="District",
        y="lane_miles",
        color="lane_type",
        barmode="group",
        category_orders={"lane_type": list(LANE_TYPE_COLORS)},
        color_discrete_map=LANE_TYPE_COLORS,
        labels={"District": "District", "lane_miles": "Lane-miles", "lane_type": "Lane type"},
    )
    fig_lanes.update_layout(
        font=dict(color="#1a1a19"),
        xaxis=dict(type="category", title="District"),
        yaxis=dict(title="Lane-miles"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=380,
        legend=dict(orientation="h", y=1.15),
    )
    st.plotly_chart(fig_lanes, width='stretch', theme=None)


# ------------------------------------------- Row 7: Weigh Stations --

st.subheader("Weigh Stations (Commercial Vehicle Enforcement)")
st.caption(
    "Truck weigh/inspection station locations from Caltrans' Vehicle Enforcement "
    "Facilities layer. Kept simple for now - a per-district count and a location "
    "map, not yet cross-referenced against Truck AADT corridors for a coverage "
    "analysis."
)

ws_col1, ws_col2 = st.columns([1, 2])

with ws_col1:
    station_counts = weigh_stations.groupby("District").size().reset_index(name="station_count")
    ws_bar_colors = [
        SERIES_BLUE if (not district_is_filtered or d == selected_district) else MUTED_GRAY
        for d in station_counts["District"]
    ]
    fig_ws_count = px.bar(
        station_counts,
        x="District",
        y="station_count",
        labels={"District": "District", "station_count": "Stations"},
    )
    fig_ws_count.update_traces(marker_color=ws_bar_colors)
    fig_ws_count.update_layout(
        font=dict(color="#1a1a19"),
        xaxis=dict(type="category", title="District"),
        yaxis=dict(title="Stations"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=360,
    )
    st.plotly_chart(fig_ws_count, width='stretch', theme=None)

with ws_col2:
    if stations_f.empty:
        st.info(f"No weigh stations recorded in District {selected_district}.")
    else:
        if district_is_filtered:
            _map_center = {"lat": stations_f["Latitude"].mean(), "lon": stations_f["Longitude"].mean()}
            _map_zoom = 6.5
        else:
            _map_center = {"lat": 37.2, "lon": -119.5}
            _map_zoom = 4.6

        fig_ws_map = px.scatter_map(
            stations_f,
            lat="Latitude",
            lon="Longitude",
            hover_name="FACILITY_NAME",
            hover_data={
                "District": True, "ROUTE": True, "DIRECTION": True,
                "Latitude": False, "Longitude": False,
            },
            color_discrete_sequence=[SERIES_BLUE],
            zoom=_map_zoom,
            center=_map_center,
            height=360,
            map_style="carto-positron",
        )
        fig_ws_map.update_traces(marker=dict(size=11))
        fig_ws_map.update_layout(
            font=dict(color="#1a1a19"),
            margin=dict(l=0, r=0, t=0, b=0),
        )
        st.plotly_chart(fig_ws_map, width='stretch', theme=None)


# ------------------------------------------------------- Row 8: Bridges --

st.subheader("Bridges")
st.caption(
    "State Highway + Local bridges, from Suz's own Caltrans GIS Data Hub "
    "extract (data/bridges_combined.csv - a static input, unlike every other "
    "section on this page; see the module docstring). No condition/"
    "sufficiency rating exists in this source - age and structure type are "
    "used as proxies, not a safety assessment. This is a second, integrated "
    "view of the same data as Suz's standalone bridge dashboard "
    "(caltransbridgedashboard.streamlit.app), which stays live separately."
)

if bridges_f.empty:
    st.info(
        f"No bridges recorded in District {selected_district}."
        if district_is_filtered else "No bridge data available."
    )
else:
    pre_1970 = (bridges_f["YRBLT"] < 1970).sum()
    avg_age = (CURRENT_YEAR - bridges_f["YRBLT"]).mean()

    bkpi1, bkpi2, bkpi3, bkpi4, bkpi5 = st.columns(5)
    bkpi1.metric("Total Bridges", f"{len(bridges_f):,}")
    bkpi2.metric("State Highway", f"{(bridges_f['Bridge_Type'] == 'State Highway').sum():,}")
    bkpi3.metric("Local", f"{(bridges_f['Bridge_Type'] == 'Local').sum():,}")
    bkpi4.metric("Avg. Age (yrs)", f"{avg_age:,.0f}")
    bkpi5.metric(
        "Built Before 1970", f"{pre_1970:,}",
        help="Common reference point for design-life review, not a condition rating.",
    )

    bridge_map_col, bridge_dist_col = st.columns([1.3, 1])

    with bridge_map_col:
        st.caption("Bridge Locations")
        if district_is_filtered:
            _b_center = {"lat": bridges_f["LAT"].mean(), "lon": bridges_f["LON"].mean()}
            _b_zoom = 6.5
        else:
            _b_center = {"lat": 37.2, "lon": -119.5}
            _b_zoom = 4.6

        fig_bridge_map = px.scatter_map(
            bridges_f,
            lat="LAT",
            lon="LON",
            color="Bridge_Type",
            color_discrete_map=BRIDGE_TYPE_COLORS,
            category_orders={"Bridge_Type": list(BRIDGE_TYPE_COLORS)},
            hover_name="NAME",
            hover_data={
                "District": True, "County_Name": True, "YRBLT": True,
                "LAT": False, "LON": False,
            },
            labels={"Bridge_Type": "Bridge type"},
            center=_b_center,
            zoom=_b_zoom,
            height=460,
            opacity=0.6,
            map_style="carto-positron",
        )
        fig_bridge_map.update_layout(
            font=dict(color="#1a1a19"),
            margin=dict(l=0, r=0, t=0, b=0),
            legend=dict(orientation="h", y=1.05),
        )
        st.plotly_chart(fig_bridge_map, width='stretch', theme=None)

    with bridge_dist_col:
        st.caption("Bridges by District")
        dist_counts = bridges_f.groupby("District", observed=True).size().reset_index(name="bridge_count")
        fig_bridge_dist = px.bar(
            dist_counts,
            x="bridge_count",
            y="District",
            orientation="h",
            color_discrete_sequence=[SERIES_BLUE],
            labels={"bridge_count": "Bridges", "District": ""},
        )
        fig_bridge_dist.update_layout(
            font=dict(color="#1a1a19"),
            yaxis=dict(type="category", autorange="reversed"),
            xaxis=dict(title="Bridges"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=460,
            showlegend=False,
        )
        st.plotly_chart(fig_bridge_dist, width='stretch', theme=None)

    bridge_decade_col, bridge_material_col = st.columns(2)

    with bridge_decade_col:
        st.caption("Bridges by Decade Built")
        decade_counts = bridges_f.groupby("Decade_Built", as_index=False).size()
        fig_bridge_decade = px.bar(
            decade_counts,
            x="Decade_Built",
            y="size",
            color_discrete_sequence=[SERIES_BLUE],
            labels={"Decade_Built": "Decade built", "size": "Bridges"},
        )
        fig_bridge_decade.update_layout(
            font=dict(color="#1a1a19"),
            xaxis=dict(title="Decade built"),
            yaxis=dict(title="Bridges"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=360,
            showlegend=False,
        )
        st.plotly_chart(fig_bridge_decade, width='stretch', theme=None)

    with bridge_material_col:
        st.caption("Bridges by Primary Material")
        mat_counts = (
            bridges_f["MATERIAL_MAIN"].value_counts()
            .rename_axis("Material").reset_index(name="count")
            .head(8)
        )
        fig_bridge_mat = px.bar(
            mat_counts,
            x="count",
            y="Material",
            orientation="h",
            color_discrete_sequence=[SERIES_BLUE],
            labels={"count": "Bridges", "Material": ""},
        )
        fig_bridge_mat.update_layout(
            font=dict(color="#1a1a19"),
            # automargin: MATERIAL_MAIN's source values are long ("2: Concrete
            # Cont[inuous]", "0: Prstr[essed] Conc[rete] Cont[inuous]") and get
            # clipped against the fixed left margin below without this.
            yaxis=dict(autorange="reversed", automargin=True),
            xaxis=dict(title="Bridges"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=360,
            showlegend=False,
        )
        st.plotly_chart(fig_bridge_mat, width='stretch', theme=None)

    st.caption("15 Oldest Bridges" + (f" (District {selected_district})" if district_is_filtered else " (Statewide)"))
    oldest = (
        bridges_f.sort_values("YRBLT")
        .loc[:, ["NAME", "Bridge_Type", "District", "County_Name", "YRBLT", "MATERIAL_MAIN", "DESIGN_MAIN"]]
        .head(15)
        .rename(columns={"YRBLT": "Year Built"})
    )
    st.dataframe(oldest, width='stretch', hide_index=True)