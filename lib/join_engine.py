"""
Member D — Join & Arithmetic Engine

Takes:
  - contract_terms (list of dicts, matching Member C's extraction schema)
  - config_extract.csv (what the claims system is configured to pay)
  - claims_pull.csv (what actually got paid)

Produces one reconciliation row per claim, with:
  - claims_delta   = paid_amount - contract_allowed_amount   (money that already moved)
  - config_delta   = configured_rate - contract_allowed_amount (standing config error)
  - status         = "clean" | "flagged" | "needs_review"
  - ticket_text    = human-readable draft for the config/PI team

Design rules carried over from the mentor's guardrails:
  - Never combine claims_delta and config_delta into one number.
  - Never infer a missing field (e.g. blank time_of_service) — route to needs_review.
  - This module never writes to any external system. Its output is data + text only.
"""

import json
import pandas as pd


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_contract_terms(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def load_config_extract(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["configured_rate"] = pd.to_numeric(df["configured_rate"])
    return df


def load_claims_pull(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    df["billed_amount"] = pd.to_numeric(df["billed_amount"])
    df["paid_amount"] = pd.to_numeric(df["paid_amount"])
    return df


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def _resolve_effective_dated(base_df: pd.DataFrame, source_df: pd.DataFrame, keys: list[str],
                              value_cols: list[str]) -> pd.DataFrame:
    """
    Join base_df to source_df on `keys`, then resolve down to exactly ONE row
    per original base_df row (tracked via _claim_row_id), using effective-dating:

      - A source row only counts as a match if the claim's date_of_service falls
        inside that row's effective_date/end_date range.
      - If more than one source row is valid for the same key (e.g. an original
        contract term plus a later amendment, or a config rate that changed
        over time), keep only the one with the most recent effective_date --
        the version actually in force on that date of service.
      - If no source row is valid (no key match, or key matched but no version
        covers that date), the row is kept with value_cols set to null rather
        than dropped -- this is what lets needs_review flag it downstream,
        same principle as before, just now guaranteed to be a single row.

    NOTE: source_df's effective_date/end_date are always renamed to private,
    unique column names before merging. This function is called twice in a
    row (once for contract terms, once for config rates), and both sources
    use the column names "effective_date"/"end_date". Without renaming, the
    second call's merge would collide with the first call's leftover
    effective_date/end_date columns already sitting in base_df -- pandas'
    default suffix behavior keeps the LEFT (stale, first-stage) column
    unsuffixed, so the second call would silently read the wrong dates and
    filter/sort against them instead of the real config dates.
    """
    source_df = source_df.rename(columns={"effective_date": "_src_effective_date", "end_date": "_src_end_date"})
    merged = base_df.merge(source_df, on=keys, how="left")

    dos = pd.to_datetime(merged["date_of_service"])
    eff = pd.to_datetime(merged["_src_effective_date"])
    end = pd.to_datetime(merged["_src_end_date"])

    matched = eff.notna()
    in_range = matched & (dos >= eff) & (end.isna() | (dos <= end))

    invalidate_cols = value_cols + ["_src_effective_date", "_src_end_date"]
    merged.loc[matched & ~in_range, invalidate_cols] = None

    merged["_has_value"] = merged["_src_effective_date"].notna()
    merged["_eff_sort"] = pd.to_datetime(merged["_src_effective_date"])
    merged = merged.sort_values(
        by=["_claim_row_id", "_has_value", "_eff_sort"],
        ascending=[True, False, False],
    )
    merged = merged.drop_duplicates(subset=["_claim_row_id"], keep="first")
    merged = merged.sort_values("_claim_row_id").reset_index(drop=True)
    return merged.drop(columns=["_has_value", "_eff_sort", "_src_effective_date", "_src_end_date"])


def join_sources(contract_terms: list[dict], config_df: pd.DataFrame, claims_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join key: tin + cpt_code + product, resolved to the single contract term
    and single config rate actually in force on the claim's date_of_service.
    If either side has more than one version sharing that key (an amendment,
    or a rate that changed over time), only the most recent applicable
    version is kept -- never a duplicated row per version. A claim with no
    valid version on either side keeps its row with those fields null, which
    determine_status() below turns into a needs_review row rather than
    dropping it silently.
    """
    contract_df = pd.DataFrame(contract_terms)
    for col in ("tin", "cpt_code", "product"):
        contract_df[col] = contract_df[col].astype(str)

    claims_df = claims_df.reset_index(drop=True).copy()
    claims_df["_claim_row_id"] = claims_df.index

    merged = _resolve_effective_dated(
        claims_df, contract_df,
        keys=["tin", "cpt_code", "product"],
        value_cols=["clause_reference", "contract_allowed_amount", "modifiers",
                    "source_file", "extraction_confidence"],
    )
    merged = _resolve_effective_dated(
        merged, config_df,
        keys=["tin", "cpt_code", "product"],
        value_cols=["configured_rate"],
    )

    return merged.drop(columns=["_claim_row_id"])


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------

def compute_deltas(row: pd.Series) -> pd.Series:
    if pd.notna(row.get("contract_allowed_amount")):
        row["claims_delta"] = round(row["paid_amount"] - row["contract_allowed_amount"], 2)
    else:
        row["claims_delta"] = None

    if pd.notna(row.get("contract_allowed_amount")) and pd.notna(row.get("configured_rate")):
        row["config_delta"] = round(row["configured_rate"] - row["contract_allowed_amount"], 2)
    else:
        row["config_delta"] = None

    return row


# ---------------------------------------------------------------------------
# Status determination — the needs_review guardrail lives here
# ---------------------------------------------------------------------------

def determine_status(row: pd.Series) -> pd.Series:
    if pd.isna(row.get("contract_allowed_amount")):
        row["status"] = "needs_review"
        row["reason"] = "No matching contract term found for this TIN / CPT / product / date."
        return row

    if pd.isna(row.get("configured_rate")):
        row["status"] = "needs_review"
        row["reason"] = "No matching config extract found for this TIN / CPT / product."
        return row

    confidence = row.get("extraction_confidence")
    if pd.notna(confidence) and confidence < 0.7:
        row["status"] = "needs_review"
        row["reason"] = f"Contract extraction confidence too low ({confidence:.2f}) to trust the terms."
        return row

    modifiers = row.get("modifiers") or []
    time_of_service = row.get("time_of_service")
    time_missing = pd.isna(time_of_service) or str(time_of_service).strip() == ""
    for m in modifiers:
        condition = m.get("condition", "")
        if "time_of_service" in condition and time_missing:
            row["status"] = "needs_review"
            row["reason"] = (
                f"time_of_service is blank; cannot verify eligibility for the "
                f"'{m.get('type')}' modifier ({condition})."
            )
            return row

    if abs(row["claims_delta"]) > 0.01 or abs(row["config_delta"]) > 0.01:
        row["status"] = "flagged"
        if abs(row["config_delta"]) > 0.01:
            row["reason"] = (
                f"Configured rate (${row['configured_rate']:.2f}) does not match contract "
                f"allowed (${row['contract_allowed_amount']:.2f}) — standing config error."
            )
        else:
            row["reason"] = (
                f"Claim paid (${row['paid_amount']:.2f}) does not match contract "
                f"allowed (${row['contract_allowed_amount']:.2f})."
            )
        return row

    row["status"] = "clean"
    row["reason"] = "Paid amount matches contract allowed; config matches contract."
    return row


# ---------------------------------------------------------------------------
# Ticket text
# ---------------------------------------------------------------------------

def generate_ticket_text(row: pd.Series) -> str:
    allowed = row.get("contract_allowed_amount")
    configured = row.get("configured_rate")
    allowed_str = f"${allowed:.2f}" if pd.notna(allowed) else "n/a"
    configured_str = f"${configured:.2f}" if pd.notna(configured) else "n/a"
    claims_delta_str = f"${row['claims_delta']:.2f}" if pd.notna(row.get("claims_delta")) else "n/a"
    config_delta_str = f"${row['config_delta']:.2f}" if pd.notna(row.get("config_delta")) else "n/a"
    clause = row.get("clause_reference")
    clause = clause if pd.notna(clause) else "n/a"

    return f"""Reconciliation flag — {row['tin']} / CPT {row['cpt_code']} / {row['product']}
Claim: {row['claim_id']}, DOS {row['date_of_service']}

Billed: ${row['billed_amount']:.2f}
Paid: ${row['paid_amount']:.2f}
Contract allowed ({clause}): {allowed_str}
Configured rate: {configured_str}

Claims-level delta (paid - contract allowed): {claims_delta_str}
Config-level delta (configured - contract allowed): {config_delta_str}

Status: {row['status']}
Reason: {row['reason']}

Disposition: [ awaiting analyst review — load correction / recoup / send back ]
"""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def reconcile(contract_terms: list[dict], config_df: pd.DataFrame, claims_df: pd.DataFrame) -> pd.DataFrame:
    """
    Core reconciliation logic, no file I/O. This is the function Member B's API
    should call directly: load the uploaded files however the API layer wants
    (disk, memory, wherever), then pass the resulting objects straight in here.
    """
    merged = join_sources(contract_terms, config_df, claims_df)
    merged = merged.apply(compute_deltas, axis=1)
    merged = merged.apply(determine_status, axis=1)
    merged["ticket_text"] = merged.apply(generate_ticket_text, axis=1)

    output_cols = [
        "claim_id", "tin", "cpt_code", "product", "date_of_service", "time_of_service",
        "billed_amount", "paid_amount", "contract_allowed_amount", "configured_rate",
        "claims_delta", "config_delta", "status", "clause_reference", "reason", "ticket_text",
    ]
    return merged[output_cols]


def run_reconciliation(contract_terms_path: str, config_extract_path: str, claims_pull_path: str) -> pd.DataFrame:
    """
    Convenience wrapper for local testing/demos: loads all three inputs from
    disk, then calls reconcile(). Member B's API should call reconcile()
    directly instead of this, since it will already have data loaded in memory.
    """
    contract_terms = load_contract_terms(contract_terms_path)
    config_df = load_config_extract(config_extract_path)
    claims_df = load_claims_pull(claims_pull_path)
    return reconcile(contract_terms, config_df, claims_df)


if __name__ == "__main__":
    results = run_reconciliation(
        "contract_terms_reference.json",
        "config_extract.csv",
        "claims_pull.csv",
    )
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(results[["claim_id", "contract_allowed_amount", "configured_rate",
                    "claims_delta", "config_delta", "status"]].to_string(index=False))
    results.to_csv("reconciliation_results.csv", index=False)
    print("\nSaved full results (including ticket_text) to reconciliation_results.csv")