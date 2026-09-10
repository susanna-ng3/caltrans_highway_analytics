# Caltrans Highway Analytics Dashboard

A single-page Streamlit dashboard combining eight analyses, seven built from Caltrans'
public ArcGIS REST FeatureServer layers pulled live — no manual downloads, no ArcGIS
login, no static extracts checked in except the cleaned outputs the dashboard reads —
plus one (Bridges) that's an intentional exception; see below.

**Live dashboard:** https://cthighwayanalytics.streamlit.app/

## What's in it

- **Highway Mileage by District** — centerline miles per district from State Highway
  Network (SHN) Lines, one alignment per segment (Right / Right Independent) to avoid
  double-counting divided highways.
- **Traffic Volume** — Annual AADT and Truck AADT, unpivoted to one row per
  direction-segment, ranked within each district, cross-referenced against SHN Lines
  for route type (State / US / Interstate).
- **Congestion Bottlenecks** — top-10-per-district bottleneck ranking by delay,
  spatially joined to SHN Lines for route context.
- **Climate Risk Overlay (CCVRA)** — present-day risk tier (Negligible → High) on
  non-bridge roadway segments for four Climate Change Vulnerability and Risk
  Assessment hazards: Wildfire, Landslide, Riverine Flood, and Coastal Flood.
- **District Overview Map** — the four district-level metrics above (centerline miles,
  avg. AADT, bottleneck delay, climate risk %), as a choropleth over Caltrans' 12
  district boundaries with a metric selector, so they can be compared geographically
  instead of only bar-by-bar.
- **Managed Lanes (HOV & Express Lanes)** — lane-miles per district, HOV vs. Express
  Lane, shown together since many Express Lanes are converted HOV lanes. HOV segments
  only carry a county in Caltrans' data, so those are assigned a district via a
  county-to-district crosswalk built from SHN Lines.
- **Weigh Stations** — a per-district count and location map of Commercial Vehicle
  Enforcement Facilities (truck weigh/inspection stations).
- **Bridges** — State Highway + Local bridge inventory (KPI row, location map, and
  breakdowns by district / decade built / primary material, plus an oldest-bridges
  table), merged in from Suz's own pre-cleaned extract rather than a live pull —
  see "The one static exception: Bridges" below.

A shared District filter drives every section; a Route Type filter narrows Traffic
Volume and Bottlenecks (the only two with a RouteType column). See the docstring at
the top of `app.py` for the exact filtering behavior per section.

### The one static exception: Bridges

Every other section pulls live from Caltrans' ArcGIS REST services. Bridges doesn't:
`data/bridges_combined.csv` is Suz's own pre-cleaned 25,862-row bridge inventory
(State Highway + Local), the same file her separate, already-deployed
[bridge dashboard](https://caltransbridgedashboard.streamlit.app/) reads from
`D:\Bridges\Bridges_Combined.csv`. Agreed with Suz (2026-09-09) to use it as-is rather
than write a live-pulling `clean_bridges.py`, since the file is already clean and its
`DIST` column already matches this project's 1-12 district numbering exactly — no
crosswalk needed. That standalone dashboard stays live separately; this section is a
second, integrated view of the same data using the shared District filter, not a
replacement. Its `Bridge_Type` coloring (State Highway / Local) deliberately does NOT
reuse the blue/orange the standalone dashboard uses for that same field, since this
page already uses blue/orange for RouteType (State/US) — see the `BRIDGE_TYPE_COLORS`
comment in `app.py`.

## Repo structure

```
app.py                          Streamlit dashboard (reads data/*.csv, no live queries)

clean_shn_lines.py               -> data/district_mileage.csv
clean_bottlenecks.py             -> data/bottlenecks_joined.csv
clean_traffic_volume.py          -> data/aadt_by_direction.csv, data/truck_aadt.csv
clean_ccvra_risk.py              -> data/ccvra_risk_by_district.csv,
                                     data/ccvra_asset_type_breakdown.csv
clean_district_boundaries.py     -> data/district_boundaries.geojson, data/district_metrics.csv
clean_managed_lanes.py           -> data/managed_lanes_by_district.csv
clean_weigh_stations.py          -> data/weigh_stations.csv

data/bridges_combined.csv        Static input (NOT a clean_*.py output) - see
                                  "The one static exception: Bridges" above

test_clean_shn_lines.py
test_clean_bottlenecks.py
test_clean_traffic_volume.py
test_clean_ccvra_risk.py
test_clean_district_boundaries.py
test_clean_managed_lanes.py
test_clean_weigh_stations.py

export_static_preview.py        Builds a non-interactive dashboard_preview.html for a
                                 quick before-you-deploy visual check (no Streamlit needed)

requirements.txt
data/                            Cleaned CSVs the dashboard reads, plus real-data
                                 fixtures the test suite uses
```

## How the data pipeline works

Each `clean_*.py` script is standalone: it queries the relevant Caltrans FeatureServer(s)
live, applies the transformations documented in its own module docstring (unpivoting,
deduplication, route-type joins, risk-tier filtering, etc.), and writes one or more CSVs
to `data/`. `app.py` only ever reads those CSVs — it never queries ArcGIS directly, so the
dashboard loads fast and doesn't depend on Caltrans' servers being up. The one exception
is `clean_district_boundaries.py`'s district_metrics.csv output, which re-reads the other
four scripts' already-written CSVs instead of hitting a new endpoint for those metrics -
see its module docstring.

Source layers (all public, no authentication required):

| Script | Source FeatureServer(s) |
|---|---|
| `clean_shn_lines.py` | CHhighway/SHN_Lines |
| `clean_bottlenecks.py` | Caltrans bottleneck layer + CHhighway/SHN_Lines |
| `clean_traffic_volume.py` | CHhighway/Traffic_AADT, CHhighway/Truck_Volumes_AADT, CHhighway/SHN_Lines |
| `clean_ccvra_risk.py` | CCVRA/CCVRA_Wildfire_Risk, CCVRA_Landslide_Risk, CCVRA_Riverine_Flood_Risk, CCVRA_Coastal_Flood_Risk |
| `clean_district_boundaries.py` | CHboundary/District_Tiger_Lines (+ re-reads 4 existing data/*.csv files) |
| `clean_managed_lanes.py` | CHhighway/HOV, CHhighway/Express_Lanes (+ CHhighway/SHN_Lines for the county/district crosswalk) |
| `clean_weigh_stations.py` | CHhighway/Vehicle_Enforcement_Facilities |
| _(none - static input)_ | `data/bridges_combined.csv`, see "The one static exception: Bridges" above |

Design decisions and real-data edge cases each script handles (divided-highway
coordinate jitter, postmile-prefix ambiguity, field-type mismatches between
independently-published layers, the CCVRA current-risk vs. sea-level-rise scenario
split, etc.) are documented inline in that script's module docstring — read that first
if you're modifying one.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt

streamlit run app.py
```

Streamlit will open the dashboard in your browser (usually `http://localhost:8501`).
Running `python app.py` directly instead of `streamlit run app.py` will print
"missing ScriptRunContext" warnings — that's expected bare-mode behavior, not an error,
it just means Streamlit isn't rendering anything because it wasn't launched as a
Streamlit app.

## Regenerating the data

Each pipeline script can be re-run independently to refresh its CSV(s) with current
data:

```bash
python clean_shn_lines.py
python clean_bottlenecks.py
python clean_traffic_volume.py
python clean_ccvra_risk.py
python clean_managed_lanes.py
python clean_weigh_stations.py
python clean_district_boundaries.py   # run this one LAST - it re-reads the other four's CSVs
```

All of these hit live Caltrans endpoints and can take anywhere from a few seconds to a
couple of minutes depending on layer size (the CCVRA layers aggregate ~1.2M rows
server-side per hazard). No API keys or credentials needed. `data/bridges_combined.csv`
has no regeneration command - it's Suz's own static extract; replace the file directly
if she sends a refreshed one.
`clean_district_boundaries.py` is the exception among the live-pulling scripts - run it after the other four (or just
after `clean_shn_lines.py`, `clean_bottlenecks.py`, `clean_traffic_volume.py`, and
`clean_ccvra_risk.py` have already been run at least once), since its district_metrics.csv
output re-reads their CSVs from `data/` rather than pulling that data itself.

## Testing

```bash
pytest -v
```

Tests are built from real data pulled from the live layers (not synthetic geometry),
with a few real-data-only tests skipped automatically if the corresponding fixture
CSV isn't present in `data/`.

## Previewing before you deploy

```bash
python export_static_preview.py
```

Writes `dashboard_preview.html` — a static, non-interactive snapshot of all eight
sections (statewide view) you can open directly in a browser with no server running.
Useful for a quick visual/color check before pushing changes live. It's not part of
the deployed app and doesn't need to be committed to the repo.

## Data sources

Seven of the eight sections are pulled from Caltrans' public GIS services
([caltrans-gis.dot.ca.gov](https://caltrans-gis.dot.ca.gov/arcgis/rest/services)); no
data is redistributed beyond the cleaned, derived CSVs in `data/` that this dashboard
itself reads. Bridges is the one exception — `data/bridges_combined.csv` is Suz's own
pre-cleaned extract, not a live pull; see "The one static exception: Bridges" above.
