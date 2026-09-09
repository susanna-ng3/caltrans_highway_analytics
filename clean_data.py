"""
clean_bridges.py

Combine Caltrans "State Highway Bridges" and "Local Bridges" CSV exports
(pulled from the Caltrans GIS Hub FeatureServer layers) into one clean,
analysis-ready CSV.

Expected input files (edit DATA_DIR / filenames below if yours differ):
    D:\\Bridges\\State_Highway_Bridges.csv
    D:\\Bridges\\Local_Bridges.csv

Output:
    D:\\Bridges\\Bridges_Combined.csv

Run:
    python clean_bridges.py
"""

import pandas as pd
from pathlib import Path

# ---------------------------------------------------------------------------
# Config -- adjust paths here if your filenames differ
# ---------------------------------------------------------------------------
DATA_DIR = Path(r"D:\Bridges")
STATE_HWY_FILE = DATA_DIR / "State_Highway_Bridges.csv"
LOCAL_FILE = DATA_DIR / "Local_Bridges.csv"
OUTPUT_FILE = DATA_DIR / "Bridges_Combined.csv"

CURRENT_YEAR = 2026  # used to compute Age -- update in future years

# Caltrans county code -> full county name.
# (Standard 2-3 letter Caltrans county abbreviations used in DIST/CO fields.)
COUNTY_CODES = {
    "ALA": "Alameda", "ALP": "Alpine", "AMA": "Amador", "BUT": "Butte",
    "CAL": "Calaveras", "COL": "Colusa", "CC": "Contra Costa", "DN": "Del Norte",
    "ED": "El Dorado", "FRE": "Fresno", "GLE": "Glenn", "HUM": "Humboldt",
    "IMP": "Imperial", "INY": "Inyo", "KER": "Kern", "KIN": "Kings",
    "LAK": "Lake", "LAS": "Lassen", "LA": "Los Angeles", "MAD": "Madera",
    "MRN": "Marin", "MPA": "Mariposa", "MEN": "Mendocino", "MER": "Merced",
    "MOD": "Modoc", "MNO": "Mono", "MON": "Monterey", "NAP": "Napa",
    "NEV": "Nevada", "ORA": "Orange", "PLA": "Placer", "PLU": "Plumas",
    "RIV": "Riverside", "SAC": "Sacramento", "SBT": "San Benito",
    "SBD": "San Bernardino", "SD": "San Diego", "SF": "San Francisco",
    "SJ": "San Joaquin", "SLO": "San Luis Obispo", "SM": "San Mateo",
    "SB": "Santa Barbara", "SCL": "Santa Clara", "SCR": "Santa Cruz",
    "SHA": "Shasta", "SIE": "Sierra", "SIS": "Siskiyou", "SOL": "Solano",
    "SON": "Sonoma", "STA": "Stanislaus", "SUT": "Sutter", "TEH": "Tehama",
    "TRI": "Trinity", "TUL": "Tulare", "TUO": "Tuolumne", "VEN": "Ventura",
    "YOL": "Yolo", "YUB": "Yuba",
}

# Caltrans district number -> short label
DISTRICT_LABELS = {f"{i:02d}": f"District {i}" for i in range(1, 13)}


def load_state_highway_bridges(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    df["Bridge_Type"] = "State Highway"
    return df


def load_local_bridges(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str)
    # Local Bridges export truncates a few field names (10-char shapefile
    # limit carried over from the source layer) -- align to the State
    # Highway Bridges names before combining.
    df = df.rename(columns={
        "DESIGN_MAI": "DESIGN_MAIN",
        "MATERIAL_M": "MATERIAL_MAIN",
        "DATA_EXTRA": "DATA_EXTRACTED",
    })
    df["Bridge_Type"] = "Local"
    return df


def clean_combined(df: pd.DataFrame) -> pd.DataFrame:
    # --- numeric conversions ---
    numeric_cols = ["LAT", "LON", "YRBLT", "LENG", "MAINSPANS", "APPSPANS",
                     "DECKWIDTH", "PM"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- derived fields ---
    df["Age"] = CURRENT_YEAR - df["YRBLT"]
    df["Decade_Built"] = (df["YRBLT"] // 10 * 10).astype("Int64")

    df["County_Name"] = df["CO"].str.strip().str.upper().map(COUNTY_CODES)
    unmapped = df.loc[df["County_Name"].isna(), "CO"].dropna().unique()
    if len(unmapped):
        print(f"NOTE: {len(unmapped)} county code(s) not found in lookup: "
              f"{sorted(unmapped)} -- add them to COUNTY_CODES if needed.")

    df["District_Label"] = df["DIST"].str.strip().str.zfill(2).map(DISTRICT_LABELS)

    # Tidy the free-text material/design fields (trailing spaces are common
    # in this source data, e.g. "02: Stringer/Multi-Beam ")
    for col in ["DESIGN_MAIN", "MATERIAL_MAIN", "FAC", "NAME", "CITY"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    return df


def main():
    if not STATE_HWY_FILE.exists() or not LOCAL_FILE.exists():
        raise FileNotFoundError(
            f"Expected input files not found. Checked:\n"
            f"  {STATE_HWY_FILE}\n  {LOCAL_FILE}\n"
            f"Edit DATA_DIR / filenames at the top of this script if yours "
            f"are named differently."
        )

    shb = load_state_highway_bridges(STATE_HWY_FILE)
    loc = load_local_bridges(LOCAL_FILE)

    combined = pd.concat([shb, loc], ignore_index=True, sort=False)
    combined = clean_combined(combined)

    # Column order: put the most useful fields first for readability
    lead_cols = ["Bridge_Type", "BRIDGE", "NAME", "District_Label", "DIST",
                 "County_Name", "CO", "CITY", "RTE", "PM", "YRBLT", "Age",
                 "Decade_Built", "LAT", "LON", "LENG", "MAINSPANS",
                 "APPSPANS", "DECKWIDTH", "DESIGN_MAIN", "MATERIAL_MAIN",
                 "FAC", "LOC", "INTERSEC", "DATA_EXTRACTED"]
    lead_cols = [c for c in lead_cols if c in combined.columns]
    remaining = [c for c in combined.columns if c not in lead_cols]
    combined = combined[lead_cols + remaining]

    combined.to_csv(OUTPUT_FILE, index=False)

    # --- sanity check summary ---
    print(f"Wrote {len(combined):,} rows to {OUTPUT_FILE}")
    print(combined["Bridge_Type"].value_counts().to_string())
    print(f"Year built range: {int(combined['YRBLT'].min())}"
          f"-{int(combined['YRBLT'].max())}")
    print(f"Missing LAT/LON: {combined['LAT'].isna().sum()}")
    print(f"Missing YRBLT: {combined['YRBLT'].isna().sum()}")
    print(f"Districts found: {combined['District_Label'].nunique()}")
    print(f"Counties found: {combined['County_Name'].nunique()}")


if __name__ == "__main__":
    main()