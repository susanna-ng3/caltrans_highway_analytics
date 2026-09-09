"""
app.py - Caltrans Highway Analytics dashboard

Single-page layout combining four analyses, each built from a clean_*.py
output in data/:
  - Highway Mileage by District  (clean_shn_lines.py -> district_mileage.csv)
  - Traffic Volume               (clean_traffic_volume.py -> aadt_by_direction.csv, truck_aadt.csv)
  - Congestion Bottlenecks       (clean_bottlenecks.py -> bottlenecks_joined.csv)
  - Climate Risk Overlay (CCVRA) (clean_ccvra_risk.py -> ccvra_risk_by_district.csv)

Originally three tabs, one per analysis, while each was being built and
verified independently. Combined onto one scrolling page (agreed with Suz,
2026-09-09) now that all three (then four) are done: tabs hide the other
analyses while you're looking at one, which works against the goal of one
connected dashboard. A shared District filter drives the KPI row and every
section that carries a District column (which now includes Climate Risk);
a Route type filter (Traffic Volume and Bottlenecks both carry RouteType
from the SHN Lines join - Mileage and Climate Risk don't) narrows those two
sections further. Selecting a specific district doesn't shrink the Mileage
chart to one bar - it stays a statewide comparison, with the selected
district highlighted, since that comparison is the point of that panel.
Traffic Volume, Bottlenecks, and Climate Risk all genuinely filter down to
the selected district instead, since each is already a per-district
breakdown rather than a district-vs-district comparison.
"""

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
else:
    ccvra_f = ccvra


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
        xaxis=dict(type="category", title="District"),
        yaxis=dict(title="Centerline miles"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=360,
    )
    st.plotly_chart(fig_mileage, width='stretch')

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
            labels={"location_label": "", "AADT": "AADT", "RouteType": "Route type"},
            hover_data={"DISTRICT": True, "CNTY": True, "DESCRIPTION": True},
        )
        fig_top10.update_layout(
            xaxis=dict(categoryorder="total descending"),
            yaxis=dict(title="AADT"),
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=360,
            legend=dict(orientation="h", y=1.15),
        )
        st.plotly_chart(fig_top10, width='stretch')


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
            hover_data={
                "RTE": True, "CNTY": True, "DISTRICT": True, "TRK_PERCENT_TOT": ":.1f",
                "VEHICLE_AADT_TOTAL": ":,.0f", "TOT_TRK_AADT": ":,.0f",
            },
            labels={"VEHICLE_AADT_TOTAL": "Total vehicle AADT", "TOT_TRK_AADT": "Truck AADT", "RouteType": "Route type"},
        )
        fig_scatter.update_traces(marker=dict(size=7, opacity=0.75))
        fig_scatter.update_layout(
            plot_bgcolor="#fcfcfb",
            paper_bgcolor="#fcfcfb",
            margin=dict(t=10, b=10),
            height=380,
            legend=dict(orientation="h", y=1.15),
        )
        st.plotly_chart(fig_scatter, width='stretch')

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
        margin=dict(l=0, r=0, t=0, b=0),
        coloraxis_colorbar=dict(title="Delay<br>(veh-hrs)"),
    )
    st.plotly_chart(fig_map, width='stretch')


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
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=10, b=10),
        height=420,
        legend=dict(orientation="h", y=1.12, traceorder="normal"),
        bargap=0.3,
    )
    st.plotly_chart(fig_risk, width='stretch')