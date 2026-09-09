"""
app.py - Caltrans Highway Analytics dashboard

Two tabs, each built from a clean_*.py output in data/:
  - Highway Mileage by District  (clean_shn_lines.py -> district_mileage.csv)
  - Congestion Bottlenecks       (clean_bottlenecks.py -> bottlenecks_joined.csv)

Follows the same pattern as the Bridge Inventory dashboard: Streamlit + Plotly,
reading pre-cleaned CSVs from a data/ folder next to this script so it runs
the same way locally and on Streamlit Community Cloud.
"""

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

DATA_DIR = Path(__file__).resolve().parent / "data"

# Sequential blue ramp (light -> dark), from the shared dataviz palette -
# one hue for magnitude, reused for both the mileage bars and the
# bottleneck severity map so the two tabs read as one system.
BLUE_SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SERIES_BLUE = "#2a78d6"

st.set_page_config(page_title="Caltrans Highway Analytics", layout="wide")
st.title("Caltrans Highway Analytics")

tab_mileage, tab_bottlenecks = st.tabs(["Highway Mileage by District", "Congestion Bottlenecks"])


# ---------------------------------------------------------------- Mileage --
with tab_mileage:
    st.caption(
        "State Highway Network Lines, filtered to one alignment per segment "
        "(Right / Right Independent) and summed by eOdometer - bOdometer, "
        "grouped by Caltrans district."
    )

    mileage = pd.read_csv(DATA_DIR / "district_mileage.csv")
    mileage = mileage.sort_values("District")

    col1, col2, col3 = st.columns(3)
    col1.metric("Statewide centerline miles", f"{mileage['centerline_miles'].sum():,.0f}")
    col2.metric("Districts", f"{mileage['District'].nunique()}")
    col3.metric("Total segments", f"{mileage['segment_count'].sum():,}")

    fig_mileage = px.bar(
        mileage,
        x="District",
        y="centerline_miles",
        text="centerline_miles",
        labels={"District": "District", "centerline_miles": "Centerline miles"},
        title="Centerline miles by district",
        color_discrete_sequence=[SERIES_BLUE],
        hover_data={"segment_count": True, "centerline_miles": ":.1f"},
    )
    fig_mileage.update_traces(texttemplate="%{text:.0f}", textposition="outside")
    fig_mileage.update_layout(
        xaxis=dict(type="category", title="District"),
        yaxis=dict(title="Centerline miles"),
        plot_bgcolor="#fcfcfb",
        paper_bgcolor="#fcfcfb",
        margin=dict(t=60),
    )
    st.plotly_chart(fig_mileage, use_container_width=True)

    st.dataframe(
        mileage.rename(columns={"segment_count": "Segments", "centerline_miles": "Centerline miles"}),
        use_container_width=True,
        hide_index=True,
    )


# ----------------------------------------------------------- Bottlenecks --
with tab_bottlenecks:
    st.caption(
        "Top-10 ranked congestion bottlenecks per district, spatially joined "
        "against State Highway Network Lines for route context."
    )

    bottlenecks = pd.read_csv(DATA_DIR / "bottlenecks_joined.csv")
    bottlenecks["District"] = pd.to_numeric(bottlenecks["District"], errors="coerce").astype("Int64")

    district_options = ["All districts"] + sorted(bottlenecks["District"].dropna().unique().tolist())
    selected_district = st.selectbox("District", district_options)

    if selected_district != "All districts":
        filtered = bottlenecks[bottlenecks["District"] == selected_district]
    else:
        filtered = bottlenecks

    n_low_confidence = filtered["low_confidence_match"].sum()
    if n_low_confidence:
        st.warning(
            f"{n_low_confidence} of {len(filtered)} bottlenecks shown have a low-confidence "
            "route match (no nearby SHN Lines segment found) - route context columns are blank for those."
        )

    col1, col2 = st.columns(2)
    col1.metric("Bottlenecks shown", f"{len(filtered)}")
    col2.metric("Total delay (veh-hrs)", f"{filtered['Total_Delay__veh_hrs_'].sum():,.0f}")

    ranked_cols = [
        "District", "Rank", "County", "Fwy", "Name", "Direction",
        "Total_Delay__veh_hrs_", "Avg_Extent__Miles_", "Number_Days_Active",
        "shn_RouteType", "shn_AlignCode",
    ]
    ranked_table = (
        filtered[ranked_cols]
        .sort_values(["District", "Rank"])
        .rename(columns={
            "Total_Delay__veh_hrs_": "Total delay (veh-hrs)",
            "Avg_Extent__Miles_": "Avg extent (mi)",
            "Number_Days_Active": "Days active",
            "shn_RouteType": "Route type",
            "shn_AlignCode": "Alignment",
        })
    )
    st.dataframe(ranked_table, use_container_width=True, hide_index=True)

    mappable = filtered.dropna(subset=["latitude", "longitude"])
    if mappable.empty:
        st.info("No bottlenecks with usable coordinates for the selected district.")
    else:
        fig_map = px.scatter_map(
            mappable,
            lat="latitude",
            lon="longitude",
            color="Total_Delay__veh_hrs_",
            size="Total_Delay__veh_hrs_",
            size_max=22,
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
            height=560,
            map_style="carto-positron",
        )
        fig_map.update_layout(
            margin=dict(l=0, r=0, t=0, b=0),
            coloraxis_colorbar=dict(title="Delay<br>(veh-hrs)"),
        )
        st.plotly_chart(fig_map, use_container_width=True)