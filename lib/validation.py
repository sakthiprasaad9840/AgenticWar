"""
CSV validation.

Validates headers BEFORE any file is handed to Member D's join engine.
This is the guardrail against "malformed CSV produces silently wrong
numbers three steps downstream" — we fail at the boundary instead.

Only header/column presence is checked here, deliberately. Row-level
data quality (e.g. a blank time_of_service) is Member D's domain,
per the spec ("needs_review" flagging belongs to the join layer).
"""
import pandas as pd

class CsvValidationError(Exception):
    def __init__(self, filename: str, missing_columns: set[str]):
        self.filename = filename
        self.missing_columns = missing_columns
        super().__init__(f"{filename} is missing required columns: {sorted(missing_columns)}")

EXPECTED_CONFIG_COLUMNS = {
    "tin", "cpt_code", "product", "effective_date", "end_date", "configured_rate"
}

EXPECTED_ROSTER_COLUMNS = {
    "tin", "provider_name"
}

EXPECTED_CLAIMS_COLUMNS = {
    "claim_id", "tin", "cpt_code", "product", "date_of_service",
    "time_of_service", "place_of_service", "billed_amount", "paid_amount"
}


def validate_headers(df: pd.DataFrame, expected: set[str], filename: str) -> None:
    actual = set(df.columns)
    missing = expected - actual
    if missing:
        raise CsvValidationError(filename=filename, missing_columns=missing)


# Columns that must stay strings even when every value looks numeric.
# Without this, pandas infers cpt_code/tin as int64, and any join against
# Member C's string-typed extraction output (e.g. "99213") silently
# matches zero rows instead of raising an error — a much worse failure
# mode than a loud one, because it looks like "no mismatches found"
# instead of "the pipeline is broken."
ID_LIKE_COLUMNS = ["tin", "cpt_code", "claim_id", "product", "place_of_service", "time_of_service"]


def load_and_validate_csv(path: str, expected: set[str], filename: str) -> pd.DataFrame:
    """Read a CSV and validate its headers in one step. Raises CsvValidationError."""
    df = pd.read_csv(path, dtype={col: str for col in ID_LIKE_COLUMNS})
    validate_headers(df, expected, filename)
    return df
