"""
Phase 2 — Member D: Benchmark Engine, Feasibility Check, Scoring & Recommendation

Robust version:
- Handles proposed_allowed_amount=None safely.
- Handles missing/blank/non-numeric proposed rates.
- Never formats None with :.2f.
- Never performs arithmetic on a missing proposed rate.
- Handles zero/invalid benchmark medians safely.
- Handles insufficient historical comparables.
- Keeps feasibility evaluation ahead of benchmark recommendation.
- Produces one evaluation row per draft term.
- No external contracting-system writes are performed.
"""

import json
from typing import Any, Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

MISSING_RATE_STATUS = "missing_proposed_rate"
INSUFFICIENT_DATA_STATUS = "insufficient_data"
COMPUTED_STATUS = "computed"


def _safe_float(value: Any) -> Optional[float]:
    """
    Convert a value to float safely.

    Returns None for:
      - None
      - blank strings
      - non-numeric values
      - NaN / infinite values
    """
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None

        # Allow common currency formatting such as "$123.45" or "1,234.56".
        value = value.replace("$", "").replace(",", "")

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(number):
        return None

    return number


def _format_currency(value: Any) -> str:
    """
    Format a numeric value as currency without ever formatting None.
    """
    number = _safe_float(value)

    if number is None:
        return "Not determined"

    return f"${number:.2f}"


def _format_percentage(value: Any) -> str:
    """
    Format a percentage safely.
    """
    number = _safe_float(value)

    if number is None:
        return "Not determined"

    return f"{number:+.2f}%"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_draft_terms(path: str) -> list[dict]:
    """Load draft terms from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Draft terms JSON must contain a list of term objects.")

    return data


def load_historical_portfolio(path: str) -> pd.DataFrame:
    """
    Load historical contract data.

    contract_allowed_amount is converted to numeric. Invalid values become NaN
    and are ignored by the benchmark median calculation.
    """
    df = pd.read_csv(path, dtype=str)

    required_columns = {
        "cpt_code",
        "product",
        "specialty",
        "region",
        "contract_allowed_amount",
    }

    missing_columns = required_columns - set(df.columns)
    if missing_columns:
        raise ValueError(
            "Historical portfolio is missing required column(s): "
            + ", ".join(sorted(missing_columns))
        )

    # Normalize matching columns to strings and strip whitespace.
    for column in ["cpt_code", "product", "specialty", "region"]:
        df[column] = df[column].fillna("").astype(str).str.strip()

    df["contract_allowed_amount"] = pd.to_numeric(
        df["contract_allowed_amount"],
        errors="coerce",
    )

    return df


def load_config_rules(path: str) -> dict:
    """Load capability rules from JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Configuration rules JSON must contain an object.")

    return data


# ---------------------------------------------------------------------------
# Benchmark engine
# ---------------------------------------------------------------------------

def benchmark_rate(
    draft_term: dict,
    portfolio_df: pd.DataFrame,
    min_comparables: int = 3,
) -> dict:
    """
    Compare a draft term against historical contracts.

    Important:
    proposed_allowed_amount may legitimately be None when the extraction/
    negotiation workflow could not determine a proposed rate.

    In that case:
      - Do not calculate deviation.
      - Return benchmark_status = missing_proposed_rate.
      - Preserve the historical median if enough comparables exist.
    """

    cpt_code = str(draft_term.get("cpt_code", "")).strip()
    product = str(draft_term.get("product", "")).strip()
    specialty = str(draft_term.get("specialty", "")).strip()
    region = str(draft_term.get("region", "")).strip()

    # Validate required portfolio columns defensively.
    required_columns = {
        "cpt_code",
        "product",
        "specialty",
        "region",
        "contract_allowed_amount",
    }

    missing_columns = required_columns - set(portfolio_df.columns)
    if missing_columns:
        raise ValueError(
            "Historical portfolio is missing required column(s): "
            + ", ".join(sorted(missing_columns))
        )

    matches = portfolio_df[
        (portfolio_df["cpt_code"].astype(str).str.strip() == cpt_code)
        & (portfolio_df["product"].astype(str).str.strip() == product)
        & (portfolio_df["specialty"].astype(str).str.strip() == specialty)
        & (portfolio_df["region"].astype(str).str.strip() == region)
    ].copy()

    # Only rows with a valid historical rate can be used for a numeric benchmark.
    matches["contract_allowed_amount"] = pd.to_numeric(
        matches["contract_allowed_amount"],
        errors="coerce",
    )
    matches = matches.dropna(subset=["contract_allowed_amount"])

    comparable_count = len(matches)

    if comparable_count < min_comparables:
        return {
            "benchmark_status": INSUFFICIENT_DATA_STATUS,
            "comparable_count": comparable_count,
            "median_rate": None,
            "deviation_pct": None,
        }

    median_rate = _safe_float(matches["contract_allowed_amount"].median())
    proposed = _safe_float(draft_term.get("proposed_allowed_amount"))

    # Historical data exists, but the proposed rate is missing/invalid.
    if proposed is None:
        return {
            "benchmark_status": MISSING_RATE_STATUS,
            "comparable_count": comparable_count,
            "median_rate": round(median_rate, 2) if median_rate is not None else None,
            "deviation_pct": None,
        }

    # Prevent division by zero.
    if median_rate is None or median_rate == 0:
        return {
            "benchmark_status": "invalid_benchmark_rate",
            "comparable_count": comparable_count,
            "median_rate": median_rate,
            "deviation_pct": None,
        }

    deviation_pct = round(
        (proposed - median_rate) / median_rate * 100,
        2,
    )

    return {
        "benchmark_status": COMPUTED_STATUS,
        "comparable_count": comparable_count,
        "median_rate": round(median_rate, 2),
        "deviation_pct": deviation_pct,
    }


# ---------------------------------------------------------------------------
# Feasibility check
# ---------------------------------------------------------------------------

def check_feasibility(draft_term: dict, config_rules: dict) -> dict:
    """
    Check whether the proposed contract structure is supported by the system.

    This is a reference implementation and can be replaced by Member C's
    production feasibility function when available.
    """

    modifiers = draft_term.get("modifiers") or []

    if not isinstance(modifiers, list):
        modifiers = [modifiers]

    reasons = []

    max_modifiers = config_rules.get("max_modifiers_per_rate")

    if max_modifiers is not None:
        try:
            max_modifiers = int(max_modifiers)
        except (TypeError, ValueError):
            max_modifiers = None

    if max_modifiers is not None and len(modifiers) > max_modifiers:
        reasons.append(
            f"{len(modifiers)} modifiers proposed; system supports a maximum "
            f"of {max_modifiers} per rate."
        )

    supported_modifier_types = config_rules.get(
        "supported_modifier_types",
        [],
    )

    if not isinstance(supported_modifier_types, list):
        supported_modifier_types = []

    supported = {str(item).strip() for item in supported_modifier_types}

    unsupported = []

    for modifier in modifiers:
        if isinstance(modifier, dict):
            modifier_type = modifier.get("type")
        else:
            modifier_type = modifier

        modifier_type = str(modifier_type).strip()

        if modifier_type and modifier_type not in supported:
            unsupported.append(modifier_type)

    if unsupported:
        reasons.append(
            "Modifier type(s) not supported by current configuration: "
            + ", ".join(sorted(set(unsupported)))
            + "."
        )

    if reasons:
        return {
            "feasibility_status": "not_feasible",
            "feasibility_reason": " ".join(reasons),
        }

    return {
        "feasibility_status": "feasible",
        "feasibility_reason": (
            "Proposed structure is supported by current system configuration."
        ),
    }


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def recommend(
    benchmark: dict,
    feasibility: dict,
    deviation_threshold_pct: float = 10.0,
) -> dict:
    """
    Generate an advisory recommendation.

    Priority:
      1. Structurally not feasible -> Modify
      2. Missing proposed rate -> Modify/manual review
      3. Insufficient historical data -> Insufficient data for benchmark
      4. Invalid benchmark rate -> Insufficient data for benchmark
      5. Computed benchmark -> Accept or Negotiate
    """

    if feasibility.get("feasibility_status") == "not_feasible":
        return {
            "recommendation": "Modify",
            "reasoning": (
                f"Not feasible as proposed: "
                f"{feasibility.get('feasibility_reason', 'No reason provided.')}"
            ),
        }

    benchmark_status = benchmark.get("benchmark_status")

    if benchmark_status == MISSING_RATE_STATUS:
        median_text = _format_currency(benchmark.get("median_rate"))

        return {
            "recommendation": "Modify",
            "reasoning": (
                "The proposed allowed amount could not be determined from the "
                "draft contract. Manual review is required before a rate can be "
                f"benchmarked. Historical median, where available, is {median_text} "
                f"across {benchmark.get('comparable_count', 0)} comparable contracts."
            ),
        }

    if benchmark_status == INSUFFICIENT_DATA_STATUS:
        return {
            "recommendation": "Insufficient data for benchmark",
            "reasoning": (
                f"Only {benchmark.get('comparable_count', 0)} comparable historical "
                "contract(s) were found for this CPT/specialty/region -- not enough "
                "to benchmark reliably. "
                f"Feasibility check: {feasibility.get('feasibility_status', 'unknown')}."
            ),
        }

    if benchmark_status == "invalid_benchmark_rate":
        return {
            "recommendation": "Insufficient data for benchmark",
            "reasoning": (
                "Comparable historical contracts were found, but their median "
                "benchmark rate is zero or invalid, so a percentage deviation "
                "cannot be calculated safely."
            ),
        }

    if benchmark_status != COMPUTED_STATUS:
        return {
            "recommendation": "Modify",
            "reasoning": (
                f"Benchmark could not be completed because the benchmark status "
                f"was '{benchmark_status}'. Manual review is required."
            ),
        }

    deviation = _safe_float(benchmark.get("deviation_pct"))
    median_rate = _safe_float(benchmark.get("median_rate"))
    comparable_count = benchmark.get("comparable_count", 0)

    if deviation is None or median_rate is None:
        return {
            "recommendation": "Insufficient data for benchmark",
            "reasoning": (
                "Benchmark information was incomplete, so a reliable rate "
                "comparison could not be completed."
            ),
        }

    try:
        threshold = float(deviation_threshold_pct)
    except (TypeError, ValueError):
        threshold = 10.0

    if abs(deviation) <= threshold:
        return {
            "recommendation": "Accept",
            "reasoning": (
                f"Proposed rate is {_format_percentage(deviation)} vs. median of "
                f"{_format_currency(median_rate)} across {comparable_count} "
                "comparable contracts -- within normal range. "
                "Feasible as proposed."
            ),
        }

    return {
        "recommendation": "Negotiate",
        "reasoning": (
            f"Proposed rate is {_format_percentage(deviation)} vs. median of "
            f"{_format_currency(median_rate)} across {comparable_count} "
            "comparable contracts -- outside normal range. Feasible as proposed, "
            "but the rate itself warrants discussion."
        ),
    }


# ---------------------------------------------------------------------------
# Advisory text
# ---------------------------------------------------------------------------

def generate_advisory_text(
    term: dict,
    benchmark: dict,
    feasibility: dict,
    rec: dict,
) -> str:
    """
    Generate committee-facing advisory text.

    Critical safety behavior:
    proposed_allowed_amount is NEVER formatted directly with :.2f.
    """

    benchmark_status = benchmark.get("benchmark_status")

    if benchmark_status == COMPUTED_STATUS:
        benchmark_block = (
            f"Historical median: {_format_currency(benchmark.get('median_rate'))} "
            f"({benchmark.get('comparable_count', 0)} comparable contracts)\n"
            f"Deviation from median: {_format_percentage(benchmark.get('deviation_pct'))}"
        )

    elif benchmark_status == INSUFFICIENT_DATA_STATUS:
        benchmark_block = (
            f"Only {benchmark.get('comparable_count', 0)} comparable contract(s) "
            "found -- insufficient to benchmark reliably."
        )

    elif benchmark_status == MISSING_RATE_STATUS:
        median_rate = benchmark.get("median_rate")

        if median_rate is not None:
            benchmark_block = (
                f"Historical median: {_format_currency(median_rate)} "
                f"({benchmark.get('comparable_count', 0)} comparable contracts)\n"
                "Deviation from median: Not calculated because the proposed rate "
                "was not determined."
            )
        else:
            benchmark_block = (
                "Proposed rate was not determined, so a benchmark deviation "
                "could not be calculated."
            )

    elif benchmark_status == "invalid_benchmark_rate":
        benchmark_block = (
            f"{benchmark.get('comparable_count', 0)} comparable contract(s) found, "
            "but the historical median rate is invalid or zero. "
            "Deviation was not calculated."
        )

    else:
        benchmark_block = (
            f"Benchmark status: {benchmark_status}. "
            "A reliable benchmark deviation could not be calculated."
        )

    proposed_rate_text = _format_currency(
        term.get("proposed_allowed_amount")
    )

    return f"""Draft contract evaluation — {term.get('tin', 'Unknown TIN')} / CPT {term.get('cpt_code', 'Unknown CPT')} / {term.get('product', 'Unknown product')}
Term: {term.get('term_id', 'Unknown term')} ({term.get('specialty', 'Unknown specialty')}, {term.get('region', 'Unknown region')})
Clause: {term.get('clause_reference', 'Not specified')}

Proposed rate: {proposed_rate_text}
{benchmark_block}

Feasibility: {feasibility.get('feasibility_status', 'unknown')}
{feasibility.get('feasibility_reason', 'No feasibility reason provided.')}

Recommendation (advisory only): {rec.get('recommendation', 'Review')}
Reasoning: {rec.get('reasoning', 'No reasoning provided.')}

Committee decision: [ awaiting review — accept / negotiate / modify / reject ]
"""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def evaluate(
    draft_terms: list[dict],
    portfolio_df: pd.DataFrame,
    config_rules: dict,
) -> pd.DataFrame:
    """
    Core evaluation logic, no file I/O.

    Returns one row per draft term.
    """

    rows = []

    for term in draft_terms:
        if not isinstance(term, dict):
            continue

        benchmark = benchmark_rate(term, portfolio_df)
        feasibility = check_feasibility(term, config_rules)
        rec = recommend(benchmark, feasibility)
        advisory_text = generate_advisory_text(
            term,
            benchmark,
            feasibility,
            rec,
        )

        rows.append(
            {
                "term_id": term.get("term_id"),
                "tin": term.get("tin"),
                "cpt_code": term.get("cpt_code"),
                "specialty": term.get("specialty"),
                "region": term.get("region"),
                "proposed_allowed_amount": term.get(
                    "proposed_allowed_amount"
                ),
                "comparable_count": benchmark.get("comparable_count"),
                "median_rate": benchmark.get("median_rate"),
                "deviation_pct": benchmark.get("deviation_pct"),
                "benchmark_status": benchmark.get("benchmark_status"),
                "feasibility_status": feasibility.get("feasibility_status"),
                "feasibility_reason": feasibility.get("feasibility_reason"),
                "recommendation": rec.get("recommendation"),
                "reasoning": rec.get("reasoning"),
                "advisory_text": advisory_text,
            }
        )

    return pd.DataFrame(rows)


def evaluate_from_files(
    draft_terms_path: str,
    historical_portfolio_path: str,
    config_rules_path: str,
) -> pd.DataFrame:
    """
    Convenience wrapper for local testing/demos.
    """

    draft_terms = load_draft_terms(draft_terms_path)
    portfolio_df = load_historical_portfolio(historical_portfolio_path)
    config_rules = load_config_rules(config_rules_path)

    return evaluate(
        draft_terms,
        portfolio_df,
        config_rules,
    )


# ---------------------------------------------------------------------------
# Local test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = evaluate_from_files(
        "draft_terms_reference.json",
        "historical_portfolio.csv",
        "config_capability_rules.json",
    )

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)

    columns_to_display = [
        "term_id",
        "proposed_allowed_amount",
        "comparable_count",
        "median_rate",
        "deviation_pct",
        "benchmark_status",
        "feasibility_status",
        "recommendation",
    ]

    print(
        results[columns_to_display].to_string(index=False)
    )

    results.to_csv(
        "evaluation_results.csv",
        index=False,
    )

    print(
        "\nSaved full results (including advisory_text) "
        "to evaluation_results.csv"
    )
