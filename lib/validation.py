"""Input loading, schema normalization, and boundary validation."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd

class CsvValidationError(Exception):
    def __init__(self, filename: str, missing_columns: set[str]):
        self.filename = filename; self.missing_columns = missing_columns
        super().__init__(f"{filename} is missing required columns: {sorted(missing_columns)}")

EXPECTED_CONFIG_COLUMNS = {"tin", "cpt_code", "product", "effective_date", "end_date", "configured_rate"}
EXPECTED_ROSTER_COLUMNS = {"tin", "provider_name"}
EXPECTED_CLAIMS_COLUMNS = {"claim_id", "tin", "cpt_code", "product", "date_of_service", "time_of_service", "place_of_service", "billed_amount", "paid_amount"}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    aliases = {
        "billing_tin": "tin", "provider_tin": "tin", "cpt_hcpcs": "cpt_code",
        "CPT_HCPCS": "cpt_code", "Contracted_Rate_USD": "configured_rate",
        "Effective_Date": "effective_date", "Termination_Date": "end_date",
        "paid": "paid_amount", "billed": "billed_amount",
    }
    return df.rename(columns={k: v for k, v in aliases.items() if k in df.columns})


def load_table(path: str, kind: str) -> pd.DataFrame:
    p = Path(path); ext = p.suffix.lower()
    if ext == ".csv": df = pd.read_csv(p, dtype=str, keep_default_na=False)
    elif ext in {".xlsx", ".xls"}: df = pd.read_excel(p, sheet_name=0, dtype=str).fillna("")
    elif ext == ".json":
        with open(p, encoding="utf-8") as f: data = json.load(f)
        if isinstance(data, dict):
            for key in ("claims", "rows", "items", "data", "results", "remittances"):
                if isinstance(data.get(key), list): data = data[key]; break
        if not isinstance(data, list): raise ValueError(f"{p.name}: expected a JSON array of rows")
        df = pd.DataFrame(data)
    else: raise ValueError(f"Unsupported {kind} format: {ext}")
    return _normalize_columns(df).fillna("")


def validate_columns(df: pd.DataFrame, expected: set[str], filename: str) -> pd.DataFrame:
    missing = expected - set(df.columns)
    if missing: raise CsvValidationError(filename, missing)
    return df


def load_and_validate(path: str, expected: set[str], filename: str, kind: str) -> pd.DataFrame:
    return validate_columns(load_table(path, kind), expected, filename)


def load_and_validate_csv(path: str, expected: set[str], filename: str) -> pd.DataFrame:
    return load_and_validate(path, expected, filename, "input")


def load_config(path: str) -> pd.DataFrame:
    return load_and_validate(path, EXPECTED_CONFIG_COLUMNS, Path(path).name, "config")


def load_claims(path: str) -> pd.DataFrame:
    df = load_and_validate(path, EXPECTED_CLAIMS_COLUMNS, Path(path).name, "claims")
    for c in ["billed_amount", "paid_amount"]: df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date_of_service"] = pd.to_datetime(df["date_of_service"], errors="coerce")
    return df
