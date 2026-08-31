import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.config import settings
from lib.aava_client import extract_via_aava, extract_draft_terms_via_aava, AavaExtractionError
from lib.validation import load_and_validate_csv, EXPECTED_CONFIG_COLUMNS, EXPECTED_CLAIMS_COLUMNS
from lib.join_engine import reconcile
from lib.benchmark_engine import evaluate as evaluate_draft_terms

st.set_page_config(page_title="Spark | Contract Intelligence", page_icon="✨", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
.block-container {
    padding-top: 2.5rem;
    padding-bottom: 3rem;
    max-width: 1400px;
}
.spark-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin: 0 0 18px 0;
    padding: 8px 4px;
    width: 100%;
    overflow: visible;
}
.spark-logo {
    flex: 0 0 58px;
    width: 58px;
    height: 58px;
    border-radius: 16px;
    background: linear-gradient(135deg, #111827, #374151);
    color: #ffffff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 30px;
    box-shadow: 0 6px 18px rgba(17,24,39,.18);
}
.spark-title {
    font-size: 32px;
    font-weight: 750;
    line-height: 1.15;
    color: #111827;
    white-space: nowrap;
    overflow: visible;
}
.spark-subtitle {
    color: #6b7280;
    margin-top: 5px;
    font-size: 14px;
    white-space: nowrap;
    overflow: visible;
}
.step-card {border:1px solid #e5e7eb; border-radius:14px; padding:16px 18px; background:#fff; margin:10px 0 18px;}
.step-num {display:inline-flex; width:28px; height:28px; border-radius:50%; align-items:center; justify-content:center; background:#111827; color:white; font-weight:700; margin-right:9px;}
.step-title {font-size:20px; font-weight:700; color:#111827;}
.hint {color:#6b7280; font-size:13px; margin-top:4px;}
.file-pill {display:inline-block; padding:5px 9px; border-radius:999px; background:#f3f4f6; margin:3px; font-size:12px;}
.success-note {padding:10px 13px; border-radius:10px; background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46;}
.warning-note {padding:10px 13px; border-radius:10px; background:#fffbeb; border:1px solid #fde68a; color:#92400e;}
.advisory-note {padding:10px 13px; border-radius:10px; background:#eff6ff; border:1px solid #bfdbfe; color:#1e40af; font-size:13px;}
.footer {text-align:center; color:#9ca3af; font-size:12px; margin-top:28px;}
</style>
""", unsafe_allow_html=True)

st.markdown('''<div class="spark-header"><div class="spark-logo">✦</div><div><div class="spark-title">SPARK</div><div class="spark-subtitle">Provider Contract Intelligence • Reconcile (Phase 1) + Negotiate (Phase 2)</div></div></div>''', unsafe_allow_html=True)
st.caption("Reconcile: signed contracts vs. what was actually configured and paid. Negotiate: draft contracts vs. historical portfolio + system feasibility, before signature.")

with st.expander("Configuration", expanded=False):
    st.write(f"**AAVA endpoint:** `{settings.aava_api_base}`")
    st.write(f"**Reconcile workflow (signed PSA):** `{settings.aava_workflow_id}`")
    st.write(f"**Negotiate workflow (draft contract):** `{settings.aava_negotiate_workflow_id}`")
    if settings.aava_api_token:
        st.success("AAVA token configured")
    else:
        st.error("AAVA token is not configured. Set AAVA_API_TOKEN in .env.")

tab_reconcile, tab_negotiate = st.tabs(["📋  Reconcile", "🤝  Negotiate (Phase 2)"])


# ===========================================================================
# TAB 1 — Reconcile (Phase 1, signed contract vs config + claims)
# ===========================================================================

def safe_extract_zip(uploaded_file, destination: Path) -> list[Path]:
    """Extract a user ZIP safely and return extracted file paths."""
    destination.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(io.BytesIO(uploaded_file.getvalue())) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            member = Path(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe path inside ZIP: {info.filename}")
            # Flatten nested folders; filenames are the contract for classification.
            target = destination / member.name
            if target.exists():
                raise ValueError(f"Duplicate filename in ZIP: {member.name}")
            target.write_bytes(zf.read(info))
            extracted.append(target)
    return extracted


def classify_files(files: list[Path]) -> dict[str, Path | None]:
    result = {"psa": None, "amendment": None, "config": None, "claims": None, "roster": None}
    for path in files:
        name = path.name.upper()
        ext = path.suffix.lower()
        category = None
        if name.startswith("PSA_") and ext == ".pdf":
            category = "psa"
        elif name.startswith("AMENDMENT_") and ext == ".pdf":
            category = "amendment"
        elif name.startswith("CONFIG_") and ext == ".csv":
            category = "config"
        elif name.startswith("CLAIMS_") and ext == ".csv":
            category = "claims"
        elif name.startswith("ROSTER_") and ext == ".csv":
            category = "roster"
        if category:
            if result[category] is not None:
                raise ValueError(f"More than one {category} file found. Please keep one {category.upper()}_* file in the ZIP.")
            result[category] = path
    return result


def render_reconcile_tab():
    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">1</span><span class="step-title">Upload Input Package</span>', unsafe_allow_html=True)
    st.markdown('<div class="hint">Upload one ZIP containing the contract, extracts, and optional amendment/roster. The app identifies each file from its filename prefix.</div>', unsafe_allow_html=True)
    package = st.file_uploader("Input ZIP", type=["zip"], label_visibility="collapsed", key="reconcile_zip")
    st.markdown("**Expected filenames**")
    st.markdown('<span class="file-pill">PSA_*.pdf • required</span><span class="file-pill">CONFIG_*.csv • required</span><span class="file-pill">CLAIMS_*.csv • required</span><span class="file-pill">AMENDMENT_*.pdf • optional</span><span class="file-pill">ROSTER_*.csv • optional</span>', unsafe_allow_html=True)

    file_map = None
    if package:
        try:
            preview_dir = Path(tempfile.mkdtemp(prefix="spark_preview_"))
            extracted = safe_extract_zip(package, preview_dir)
            file_map = classify_files(extracted)
            missing = [label for label, key in [("PSA", "psa"), ("Config Extract", "config"), ("Claims Pull", "claims")] if not file_map[key]]
            if missing:
                st.error("Missing required file(s): " + ", ".join(missing))
            else:
                st.markdown('<div class="success-note">✓ Required files detected</div>', unsafe_allow_html=True)
                cols = st.columns(5)
                labels = [("PSA", "psa"), ("Amendment", "amendment"), ("Config", "config"), ("Claims", "claims"), ("Roster", "roster")]
                for col, (label, key) in zip(cols, labels):
                    with col:
                        if file_map[key]:
                            st.success(f"✓ {label}")
                            st.caption(file_map[key].name)
                        else:
                            st.info(f"— {label}")
        except zipfile.BadZipFile:
            st.error("The uploaded file is not a valid ZIP archive.")
        except Exception as exc:
            st.error(f"Could not inspect the ZIP: {exc}")

    ready = bool(package and file_map and file_map["psa"] and file_map["config"] and file_map["claims"])
    run = st.button("✨  Run Contract Validation", type="primary", disabled=not ready, use_container_width=True, key="reconcile_run")
    st.markdown('</div>', unsafe_allow_html=True)

    if not run:
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="spark_reconciliation_"))
    status_box = None
    progress = None
    try:
        ...
        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">2</span><span class="step-title">Workflow Status</span>', unsafe_allow_html=True)
        status_box = st.empty()
        progress = st.progress(0)
        status_box.info("Validating input extracts...")
        config_df = load_and_validate_csv(str(config_path), EXPECTED_CONFIG_COLUMNS, config_path.name)
        claims_df = load_and_validate_csv(str(claims_path), EXPECTED_CLAIMS_COLUMNS, claims_path.name)
        progress.progress(10)

        status_box.info("Sending Contract + Amendment to AAVA workflow...")
        progress.progress(15)
        terms = extract_via_aava(
            upload_id=temp_dir.name,
            psa_path=str(contract_path),
            amendment_path=str(amendment_path) if amendment_path else None,
            user_email="",
        )
        progress.progress(80)

        if not terms:
            raise AavaExtractionError("AAVA workflow completed but returned no contract terms. Check the AAVA workflow output/document processing result.")

        status_box.info("AAVA output received. Validating against Config Extract and Claims Pull...")
        results_df = reconcile(terms, config_df, claims_df)
        progress.progress(100)
        status_box.success(f"Completed — {len(results_df)} claim(s) processed.")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">3</span><span class="step-title">Extracted Contract Terms</span>', unsafe_allow_html=True)
        terms_df = pd.DataFrame(terms)
        if not terms_df.empty:
            st.dataframe(terms_df, use_container_width=True, hide_index=True)
        else:
            st.warning("No contract terms were extracted.")
        with st.expander("View raw AAVA output"):
            st.json(terms)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">4</span><span class="step-title">Validation Results</span>', unsafe_allow_html=True)
        if results_df.empty:
            st.warning("No validation rows were returned.")
        else:
            df = results_df.copy()
            statuses = ["flagged", "needs_review", "clean"]
            status_filter = st.multiselect("Status filter", statuses, default=statuses, key="reconcile_status_filter")
            filtered = df[df["status"].isin(status_filter)]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Flagged", int((df["status"] == "flagged").sum()))
            c3.metric("Needs Review", int((df["status"] == "needs_review").sum()))
            c4.metric("Clean", int((df["status"] == "clean").sum()))
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            st.download_button("Download Validation Results CSV", filtered.to_csv(index=False).encode("utf-8"), "validation_results.csv", "text/csv", use_container_width=True, key="reconcile_download")
        st.markdown('</div>', unsafe_allow_html=True)

    except Exception as exc:
        progress.progress(0)
        status_box.error("Processing failed")
        st.error(str(exc))
        with st.expander("Technical details"):
            st.exception(exc)


# ===========================================================================
# TAB 2 — Negotiate (Phase 2, draft contract vs historical portfolio + feasibility)
# ===========================================================================

REFERENCE_DIR = ROOT / "data" / "phase2_reference"
DEFAULT_PORTFOLIO_PATH = REFERENCE_DIR / "historical_portfolio.csv"
DEFAULT_RULES_PATH = REFERENCE_DIR / "config_capability_rules.json"

RECOMMENDATION_COLORS = {
    "Accept": "background-color: #d4edda",
    "Negotiate": "background-color: #fff3cd",
    "Modify": "background-color: #fde2e2",
    "Insufficient data for benchmark": "background-color: #e2e3e5",
}


def _load_portfolio_df(uploaded) -> pd.DataFrame:
    df = pd.read_csv(uploaded if uploaded else DEFAULT_PORTFOLIO_PATH, dtype=str)
    df["contract_allowed_amount"] = pd.to_numeric(df["contract_allowed_amount"])
    return df


def _load_config_rules(uploaded) -> dict:
    if uploaded:
        return json.load(uploaded)
    with open(DEFAULT_RULES_PATH) as f:
        return json.load(f)


def _safe_currency(value, fallback="Not determined"):
    """Format a numeric value as currency without ever formatting None/NaN."""
    if value is None:
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if pd.isna(numeric):
        return fallback
    return f"${numeric:.2f}"


def _safe_percent(value, fallback="Not determined"):
    """Format a percentage safely for Streamlit metrics."""
    if value is None:
        return fallback
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return fallback
    if pd.isna(numeric):
        return fallback
    return f"{numeric:+.1f}%"


def _ensure_term_ids(terms: list[dict]) -> list[dict]:
    """
    benchmark_engine.evaluate() keys its output on term["term_id"], but the
    live AAVA negotiate-workflow output (captured traffic, 2026-08-29) does
    not include a term_id field -- only Member D's reference fixtures do.
    Assign a stable synthetic id per row when the extractor didn't supply
    one, rather than letting evaluate() KeyError on a real AAVA response.
    """
    for i, term in enumerate(terms):
        if not term.get("term_id"):
            term["term_id"] = f"AAVA-{i+1:03d}"
    return terms


def render_negotiate_tab():
    st.markdown('<div class="advisory-note">Advisory only — every recommendation here is a starting point for the contracting committee, not an automated decision. Nothing in this tab writes to any external contracting system.</div>', unsafe_allow_html=True)
    st.write("")

    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">1</span><span class="step-title">Upload Draft Contract</span>', unsafe_allow_html=True)
    st.markdown('<div class="hint">Upload one unsigned draft contract PDF. Terms are benchmarked against the historical portfolio and checked against system feasibility rules below.</div>', unsafe_allow_html=True)
    draft_pdf = st.file_uploader("Draft contract (unsigned) PDF", type=["pdf"], label_visibility="collapsed", key="negotiate_pdf")

    with st.expander("Reference data (historical portfolio + config capability rules)", expanded=False):
        st.caption("Defaults to the standing reference dataset in data/phase2_reference/. Upload your own to override for this run only.")
        c1, c2 = st.columns(2)
        with c1:
            portfolio_upload = st.file_uploader("Historical portfolio CSV (optional override)", type=["csv"], key="negotiate_portfolio")
            st.caption(f"Default: `{DEFAULT_PORTFOLIO_PATH.relative_to(ROOT)}`")
        with c2:
            rules_upload = st.file_uploader("Config capability rules JSON (optional override)", type=["json"], key="negotiate_rules")
            st.caption(f"Default: `{DEFAULT_RULES_PATH.relative_to(ROOT)}`")

    run = st.button("🤝  Run Evaluation", type="primary", disabled=not draft_pdf, use_container_width=True, key="negotiate_run")
    st.markdown('</div>', unsafe_allow_html=True)

    if not run:
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="spark_negotiate_"))
    status_box = None
    progress = None
    try:
        draft_path = temp_dir / "draft_contract.pdf"
        draft_path.write_bytes(draft_pdf.getvalue())

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">2</span><span class="step-title">Workflow Status</span>', unsafe_allow_html=True)
        status_box = st.empty()
        progress = st.progress(0)
        status_box.info("Loading reference data (historical portfolio + config rules)...")
        portfolio_df = _load_portfolio_df(portfolio_upload)
        config_rules = _load_config_rules(rules_upload)
        progress.progress(10)

        status_box.info("Sending draft contract to AAVA negotiate workflow...")
        progress.progress(15)
        draft_terms = extract_draft_terms_via_aava(
            upload_id=temp_dir.name,
            draft_contract_path=str(draft_path),
        )
        progress.progress(70)

        if not draft_terms:
            raise AavaExtractionError("AAVA negotiate workflow completed but returned no draft terms. Check the AAVA workflow output/document processing result.")
        draft_terms = _ensure_term_ids(draft_terms)

        status_box.info("Benchmarking against historical portfolio and checking feasibility...")
        results_df = evaluate_draft_terms(draft_terms, portfolio_df, config_rules)
        progress.progress(100)
        status_box.success(f"Completed — {len(results_df)} draft term(s) evaluated.")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">3</span><span class="step-title">Extracted Draft Terms</span>', unsafe_allow_html=True)
        terms_df = pd.DataFrame(draft_terms)
        if not terms_df.empty:
            st.dataframe(terms_df, use_container_width=True, hide_index=True)
        else:
            st.warning("No draft terms were extracted.")
        with st.expander("View raw AAVA output"):
            st.json(draft_terms)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">4</span><span class="step-title">Evaluation Results</span>', unsafe_allow_html=True)
        if results_df.empty:
            st.warning("No evaluation rows were returned.")
        else:
            recs = ["Accept", "Negotiate", "Modify", "Insufficient data for benchmark"]
            present_recs = [r for r in recs if r in results_df["recommendation"].unique()]
            rec_filter = st.multiselect("Recommendation filter", present_recs, default=present_recs, key="negotiate_rec_filter")
            filtered = results_df[results_df["recommendation"].isin(rec_filter)]

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(results_df))
            c2.metric("Accept", int((results_df["recommendation"] == "Accept").sum()))
            c3.metric("Negotiate", int((results_df["recommendation"] == "Negotiate").sum()))
            c4.metric("Modify", int((results_df["recommendation"] == "Modify").sum()))

            display_cols = [c for c in [
                "term_id", "tin", "cpt_code", "specialty", "region",
                "proposed_allowed_amount", "median_rate", "deviation_pct",
                "comparable_count", "benchmark_status", "feasibility_status",
                "recommendation",
            ] if c in filtered.columns]
            styled = filtered[display_cols].style.apply(
                lambda row: [RECOMMENDATION_COLORS.get(row["recommendation"], "")] * len(row), axis=1
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
            st.download_button(
                "Download Evaluation Results CSV",
                filtered.drop(columns=["advisory_text"], errors="ignore").to_csv(index=False).encode("utf-8"),
                "evaluation_results.csv", "text/csv", use_container_width=True, key="negotiate_download",
            )
        st.markdown('</div>', unsafe_allow_html=True)

        if not results_df.empty:
            st.markdown('<div class="step-card">', unsafe_allow_html=True)
            st.markdown('<span class="step-num">5</span><span class="step-title">Evaluation Detail & Committee Decision</span>', unsafe_allow_html=True)
            selected_id = st.selectbox("Select a draft term", results_df["term_id"].tolist(), key="negotiate_selected_term")
            row = results_df[results_df["term_id"] == selected_id].iloc[0]

            st.subheader(f"Recommendation: {row['recommendation']}")
            st.caption("Advisory only — final decision rests with the contracting committee.")
            st.write(row["reasoning"])

            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                st.subheader("Benchmark")
                benchmark_status = row.get("benchmark_status")
                comparable_count = row.get("comparable_count", 0)

                # Never format a missing proposed rate with :.2f. AAVA can
                # legitimately return null when it cannot determine a rate.
                proposed_display = _safe_currency(row.get("proposed_allowed_amount"))
                median_display = _safe_currency(row.get("median_rate"))
                deviation_display = _safe_percent(row.get("deviation_pct"))

                if benchmark_status == "insufficient_data":
                    st.info(
                        f"Only {comparable_count} comparable contract(s) found — "
                        "not enough to benchmark reliably."
                    )
                elif benchmark_status == "missing_proposed_rate":
                    st.warning(
                        "The proposed allowed amount was not determined by the "
                        "AAVA workflow. Manual review is required before a rate "
                        "can be benchmarked."
                    )
                elif benchmark_status == "invalid_benchmark_rate":
                    st.warning(
                        "Comparable contracts were found, but the historical "
                        "benchmark rate is invalid or zero, so deviation cannot "
                        "be calculated."
                    )

                st.metric("Proposed rate", proposed_display)
                st.metric("Historical median", median_display)
                st.metric("Deviation", deviation_display)
                st.caption(f"Based on {comparable_count} comparable contract(s)")
            with col2:
                st.subheader("Feasibility")
                st.write(f"Status: **{row['feasibility_status']}**")
                st.write(row["feasibility_reason"])

            with st.expander("Full advisory text"):
                st.text(row["advisory_text"])

            st.divider()
            st.subheader("Committee decision")
            st.caption("Recorded locally for this session only — nothing is sent to an external contracting system.")
            key_prefix = f"committee_{selected_id}"
            decision = st.radio("Committee decision", ["accept", "negotiate", "modify", "reject"], key=f"{key_prefix}_decision", horizontal=True)
            reviewer = st.text_input("Recorded by", key=f"{key_prefix}_reviewer")
            if st.button("Record committee decision", key=f"{key_prefix}_submit"):
                if "committee_decisions" not in st.session_state:
                    st.session_state["committee_decisions"] = {}
                st.session_state["committee_decisions"][selected_id] = {
                    "decision": decision, "reviewer": reviewer or "(unspecified)",
                }
                st.success(f"Recorded: {decision} by {reviewer or '(unspecified)'}")

            if st.session_state.get("committee_decisions"):
                st.caption("Decisions recorded this session:")
                st.json(st.session_state["committee_decisions"])
            st.markdown('</div>', unsafe_allow_html=True)

    except Exception as exc:
        progress.progress(0)
        status_box.error("Processing failed")
        st.error(str(exc))
        with st.expander("Technical details"):
            st.exception(exc)


with tab_reconcile:
    render_reconcile_tab()

with tab_negotiate:
    render_negotiate_tab()

st.markdown('<div class="footer">SPARK • Provider Contract Intelligence Demo • One Streamlit application</div>', unsafe_allow_html=True)
