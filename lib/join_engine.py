import json
import logging
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Loading & Normalization
# ---------------------------------------------------------------------------

def load_contract_terms(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def load_config_extract(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["configured_rate"] = pd.to_numeric(df["configured_rate"], errors="coerce")
    return df


def load_claims_pull(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["billed_amount"] = pd.to_numeric(df["billed_amount"], errors="coerce")
    df["paid_amount"] = pd.to_numeric(df["paid_amount"], errors="coerce")
    return df


def _normalize_keys(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in keys:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.upper()
    return df


# ---------------------------------------------------------------------------
# Effective-Dated Join Logic
# ---------------------------------------------------------------------------

def _resolve_effective_dated(base_df: pd.DataFrame, source_df: pd.DataFrame, keys: list[str],
                              value_cols: list[str]) -> pd.DataFrame:
    source_df = source_df.copy()

    # Ensure effective/end date columns exist in source DataFrame prior to merge
    if "effective_date" not in source_df.columns:
        source_df["effective_date"] = None
    if "end_date" not in source_df.columns:
        source_df["end_date"] = None

    source_df = source_df.rename(columns={
        "effective_date": "_src_effective_date",
        "end_date": "_src_end_date"
    })

    # Strip whitespace and normalize case on join keys
    base_df = _normalize_keys(base_df, keys)
    source_df = _normalize_keys(source_df, keys)

    merged = base_df.merge(source_df, on=keys, how="left")

    dos = pd.to_datetime(merged["date_of_service"], errors="coerce")
    eff = pd.to_datetime(merged["_src_effective_date"], errors="coerce")
    end = pd.to_datetime(merged["_src_end_date"], errors="coerce")

    # Evaluate valid effective date windows
    has_eff = eff.notna()
    in_range = ~has_eff | ((dos.isna() | (dos >= eff)) & (end.isna() | (dos <= end)))

    # Invalidate values for rows falling outside the effective window
    invalidate_cols = [c for c in value_cols if c in merged.columns] + ["_src_effective_date", "_src_end_date"]
    merged.loc[~in_range, invalidate_cols] = None

    merged["_has_value"] = merged["_src_effective_date"].notna()
    merged["_eff_sort"] = pd.to_datetime(merged["_src_effective_date"], errors="coerce")

    # Sort to prioritize valid effective records and select the latest effective rate (amendment resolution)
    merged = merged.sort_values(
        by=["_claim_row_id", "_has_value", "_eff_sort"],
        ascending=[True, False, False],
    )
    merged = merged.drop_duplicates(subset=["_claim_row_id"], keep="first")
    merged = merged.sort_values("_claim_row_id").reset_index(drop=True)

    return merged.drop(columns=["_has_value", "_eff_sort", "_src_effective_date", "_src_end_date"])


def join_sources(contract_terms: list[dict], config_df: pd.DataFrame, claims_df: pd.DataFrame, debug: bool = False) -> pd.DataFrame:
    contract_df = pd.DataFrame(contract_terms)

    if debug:
        c_tins = contract_df["tin"].unique() if "tin" in contract_df.columns else []
        cl_tins = claims_df["tin"].unique() if "tin" in claims_df.columns else []
        logger.info(f"[DEBUG] Joining Contract TINs: {c_tins} | Claims TINs: {cl_tins}")

    # Ensure required schema fields exist in contract DataFrame
    for col in ("pricing_model", "percent_of_charge", "contract_allowed_amount"):
        if col not in contract_df.columns:
            contract_df[col] = None

    claims_df = claims_df.reset_index(drop=True).copy()
    claims_df["_claim_row_id"] = claims_df.index

    merged = _resolve_effective_dated(
        claims_df, contract_df,
        keys=["tin", "cpt_code", "product"],
        value_cols=[
            "clause_reference", "contract_allowed_amount", "pricing_model", 
            "percent_of_charge", "modifiers", "source_file", "extraction_confidence"
        ],
    )
    merged = _resolve_effective_dated(
        merged, config_df,
        keys=["tin", "cpt_code", "product"],
        value_cols=["configured_rate"],
    )

    return merged.drop(columns=["_claim_row_id"])


# ---------------------------------------------------------------------------
# Dynamic Delta Calculation
# ---------------------------------------------------------------------------

def compute_deltas(row: pd.Series) -> pd.Series:
    pricing_model = str(row.get("pricing_model")).upper() if pd.notna(row.get("pricing_model")) else ""
    
    # Calculate Percentage of Billed Charges dynamically if applicable
    if "PERCENT" in pricing_model and pd.notna(row.get("percent_of_charge")) and pd.notna(row.get("billed_amount")):
        try:
            pct = float(row["percent_of_charge"])
            billed = float(row["billed_amount"])
            row["contract_allowed_amount"] = round(billed * (pct / 100.0), 2)
        except (ValueError, TypeError):
            pass
    elif pd.notna(row.get("contract_allowed_amount")):
        try:
            row["contract_allowed_amount"] = round(float(row["contract_allowed_amount"]), 2)
        except (ValueError, TypeError):
            pass

    # Compute variance deltas independently
    if pd.notna(row.get("contract_allowed_amount")) and pd.notna(row.get("paid_amount")):
        row["claims_delta"] = round(float(row["paid_amount"]) - float(row["contract_allowed_amount"]), 2)
    else:
        row["claims_delta"] = None

    if pd.notna(row.get("contract_allowed_amount")) and pd.notna(row.get("configured_rate")):
        row["config_delta"] = round(float(row["configured_rate"]) - float(row["contract_allowed_amount"]), 2)
    else:
        row["config_delta"] = None

    return row


# ---------------------------------------------------------------------------
# Status Determination & Ticket Generator
# ---------------------------------------------------------------------------

def determine_status(row: pd.Series) -> pd.Series:
    if pd.isna(row.get("contract_allowed_amount")):
        row["status"] = "needs_review"
        row["reason"] = "No matching contract term or allowed rate found for this TIN / CPT / product / date."
        return row

    if pd.isna(row.get("configured_rate")):
        row["status"] = "needs_review"
        row["reason"] = "No matching config extract found for this TIN / CPT / product."
        return row

    confidence = row.get("extraction_confidence")
    if pd.notna(confidence) and float(confidence) < 0.7:
        row["status"] = "needs_review"
        row["reason"] = f"Contract extraction confidence too low ({float(confidence):.2f}) to trust the terms."
        return row

    modifiers = row.get("modifiers") or []
    if isinstance(modifiers, str):
        try:
            modifiers = json.loads(modifiers)
        except Exception:
            modifiers = []

    time_of_service = row.get("time_of_service")
    time_missing = pd.isna(time_of_service) or str(time_of_service).strip() == ""
    for m in modifiers:
        if isinstance(m, dict):
            condition = m.get("condition", "")
            if "time_of_service" in condition and time_missing:
                row["status"] = "needs_review"
                row["reason"] = (
                    f"time_of_service is blank; cannot verify eligibility for the "
                    f"'{m.get('type')}' modifier ({condition})."
                )
                return row

    claims_delta = row.get("claims_delta")
    config_delta = row.get("config_delta")

    if (claims_delta is not None and abs(claims_delta) > 0.01) or (config_delta is not None and abs(config_delta) > 0.01):
        row["status"] = "flagged"
        if config_delta is not None and abs(config_delta) > 0.01:
            row["reason"] = (
                f"Configured rate (${float(row['configured_rate']):.2f}) does not match contract "
                f"allowed (${float(row['contract_allowed_amount']):.2f}) — standing config error."
            )
        else:
            row["reason"] = (
                f"Claim paid (${float(row['paid_amount']):.2f}) does not match contract "
                f"allowed (${float(row['contract_allowed_amount']):.2f})."
            )
        return row

    row["status"] = "clean"
    row["reason"] = "Paid amount matches contract allowed; config matches contract."
    return row


def generate_ticket_text(row: pd.Series) -> str:
    allowed = row.get("contract_allowed_amount")
    configured = row.get("configured_rate")
    allowed_str = f"${float(allowed):.2f}" if pd.notna(allowed) else "n/a"
    configured_str = f"${float(configured):.2f}" if pd.notna(configured) else "n/a"
    claims_delta_str = f"${float(row['claims_delta']):.2f}" if pd.notna(row.get("claims_delta")) else "n/a"
    config_delta_str = f"${float(row['config_delta']):.2f}" if pd.notna(row.get("config_delta")) else "n/a"
    clause = row.get("clause_reference") if pd.notna(row.get("clause_reference")) else "n/a"
    billed_str = f"${float(row['billed_amount']):.2f}" if pd.notna(row.get("billed_amount")) else "n/a"
    paid_str = f"${float(row['paid_amount']):.2f}" if pd.notna(row.get("paid_amount")) else "n/a"

    return f"""Reconciliation flag — {row['tin']} / CPT {row['cpt_code']} / {row['product']}
Claim ID: {row['claim_id']} (PHI Tokenized under HIPAA Safe Harbor), DOS: {row['date_of_service']}

Pricing Model: {row.get('pricing_model', 'FLAT')}
Billed: {billed_str}
Paid: {paid_str}
Contract Allowed ({clause}): {allowed_str}
Configured Rate: {configured_str}

Claims-level Delta (Paid - Allowed): {claims_delta_str}
Config-level Delta (Configured - Allowed): {config_delta_str}

Status: {row['status']}
Reason: {row['reason']}

Disposition: [ awaiting analyst review — load correction / recoup / send back ]
"""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def reconcile(contract_terms: list[dict], config_df: pd.DataFrame, claims_df: pd.DataFrame, debug: bool = False) -> pd.DataFrame:
    merged = join_sources(contract_terms, config_df, claims_df, debug=debug)
    merged = merged.apply(compute_deltas, axis=1)
    merged = merged.apply(determine_status, axis=1)
    merged["ticket_text"] = merged.apply(generate_ticket_text, axis=1)

    output_cols = [
        "claim_id", "tin", "cpt_code", "product", "date_of_service", "time_of_service",
        "billed_amount", "paid_amount", "contract_allowed_amount", "pricing_model",
        "configured_rate", "claims_delta", "config_delta", "status", "clause_reference",
        "reason", "ticket_text",
    ]

    for col in output_cols:
        if col not in merged.columns:
            merged[col] = None

    return merged[output_cols]


def run_reconciliation(contract_terms_path: str, config_extract_path: str, claims_pull_path: str, debug: bool = False) -> pd.DataFrame:
    contract_terms = load_contract_terms(contract_terms_path)
    config_df = load_config_extract(config_extract_path)
    claims_df = load_claims_pull(claims_pull_path)
    return reconcile(contract_terms, config_df, claims_df, debug=debug)