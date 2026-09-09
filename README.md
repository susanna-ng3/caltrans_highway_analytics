# Caltrans Highway Analytics Dashboard

A single-page Streamlit dashboard combining four analyses built from Caltrans' public
ArcGIS REST FeatureServer layers, pulled live — no manual downloads, no ArcGIS login,
no static extracts checked into the repo except the cleaned outputs the dashboard reads.

**Live dashboard:** _add your Streamlit Community Cloud URL here once deployed_

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

A shared District filter drives all four sections; a Route Type filter narrows
Traffic Volume and Bottlenecks (the only two with a RouteType column). See the
docstring at the top of `app.py` for the exact filtering behavior per section.

## Repo structure

```
app_dashboard.py                          Streamlit dashboard (reads data/*.csv, no live queries)

clean_shn_lines.py               -> data/district_mileage.csv
clean_bottlenecks.py             -> data/bottlenecks_joined.csv
clean_traffic_volume.py          -> data/aadt_by_direction.csv, data/truck_aadt.csv
clean_ccvra_risk.py              -> data/ccvra_risk_by_district.csv,
                                     data/ccvra_asset_type_breakdown.csv

test_clean_shn_lines.py
test_clean_bottlenecks.py
test_clean_traffic_volume.py
test_clean_ccvra_risk.py

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
dashboard loads fast and doesn't depend on Caltrans' servers being up.

Source layers (all public, no authentication required):

| Script | Source FeatureServer(s) |
|---|---|
| `clean_shn_lines.py` | CHhighway/SHN_Lines |
| `clean_bottlenecks.py` | Caltrans bottleneck layer + CHhighway/SHN_Lines |
| `clean_traffic_volume.py` | CHhighway/Traffic_AADT, CHhighway/Truck_Volumes_AADT, CHhighway/SHN_Lines |
| `clean_ccvra_risk.py` | CCVRA/CCVRA_Wildfire_Risk, CCVRA_Landslide_Risk, CCVRA_Riverine_Flood_Risk, CCVRA_Coastal_Flood_Risk |

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
```

All four hit live Caltrans endpoints and can take anywhere from a few seconds to a
couple of minutes depending on layer size (the CCVRA layers aggregate ~1.2M rows
server-side per hazard). No API keys or credentials needed.

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

Writes `dashboard_preview.html` — a static, non-interactive snapshot of all four
sections (statewide view) you can open directly in a browser with no server running.
Useful for a quick visual/color check before pushing changes live. It's not part of
the deployed app and doesn't need to be committed to the repo.

## Data sources

All data is pulled from Caltrans' public GIS services
([caltrans-gis.dot.ca.gov](https://caltrans-gis.dot.ca.gov/arcgis/rest/services)).
No data is redistributed beyond the cleaned, derived CSVs in `data/` that this
dashboard itself reads.
