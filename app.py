import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path
import base64

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.config import settings
from lib.aava_client import extract_via_aava, extract_draft_terms_via_aava, AavaExtractionError
from lib.deidentify import mask_document_file
from lib.validation import load_and_validate_csv, load_config, load_claims, EXPECTED_CONFIG_COLUMNS, EXPECTED_CLAIMS_COLUMNS
from lib.join_engine import reconcile
from lib.benchmark_engine import evaluate as evaluate_draft_terms

# ===========================================================================
# PAGE CONFIGURATION & AAVA BRANDING STYLES (3.2 & 3.3)
# ===========================================================================
st.set_page_config(
    page_title="SPARK App - Powered by AAVA", 
    page_icon="✨", 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
/* App Background & Typography Alignment with AAVA */
.stApp {
    background: linear-gradient(180deg, #F3EFFB 0%, #FFFFFF 100%);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

.block-container {
    padding-top: 2rem;
    padding-bottom: 3rem;
    max-width: 1400px;
}

/* Header & Logo Components */
.spark-header {
    display: flex;
    align-items: center;
    gap: 16px;
    margin: 0 0 14px 0;
    padding: 8px 4px;
    width: 100%;
}
.aava-logo {
    height: 48px;
    width: auto;
    object-fit: contain;
}
.aava-badge {
    background-color: #000000;
    color: #FFFFFF;
    font-weight: 800;
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 16px;
    letter-spacing: 0.5px;
    display: inline-block;
}
.spark-title {
    font-size: 32px;
    font-weight: 750;
    line-height: 1.15;
    color: #111827;
}
.spark-subtitle {
    color: #6b7280;
    margin-top: 5px;
    font-size: 14px;
}

/* AAVA Callout Banner */
.aava-callout-banner {
    background: linear-gradient(90deg, #007A99 0%, #009BBF 100%);
    color: white;
    padding: 20px 24px;
    border-radius: 16px;
    margin-top: 10px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0, 122, 153, 0.15);
}

/* UI Cards & Containers */
.step-card {
    border: 1px solid #E0D7F4; 
    border-radius: 14px; 
    padding: 18px 20px; 
    background: #FFFFFF; 
    margin: 10px 0 18px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.02);
}
.step-num {
    display: inline-flex; 
    width: 28px; 
    height: 28px; 
    border-radius: 50%; 
    align-items: center; 
    justify-content: center; 
    background: #007A99; 
    color: white; 
    font-weight: 700; 
    margin-right: 9px;
}
.step-title {
    font-size: 20px; 
    font-weight: 700; 
    color: #111827;
}
.hint {
    color: #6b7280; 
    font-size: 13px; 
    margin-top: 4px;
}
.file-pill {
    display: inline-block;
    padding: 5px 9px;
    border-radius: 999px;
    background: #f3f4f6;
    margin: 3px;
    font-size: 12px;
}
.accepted-grid {
    display: grid;
    grid-template-columns: repeat(5, minmax(150px, 1fr));
    gap: 10px;
    margin: 10px 0 4px;
}
.accepted-card {
    border: 1px solid #dbe2ea;
    border-radius: 10px;
    padding: 12px 13px;
    min-height: 78px;
    background: #f8fafc;
    box-sizing: border-box;
}
.accepted-card .accepted-label {
    font-size: 13px;
    font-weight: 750;
    color: #111827;
    margin-bottom: 7px;
}
.accepted-card .accepted-type {
    display: inline-block;
    font-size: 12px;
    line-height: 1.4;
    padding: 4px 8px;
    border-radius: 7px;
    font-weight: 650;
}
.accepted-contract { border-top: 4px solid #7A52B3; }
.accepted-contract .accepted-type { background: #f1eafd; color: #5b2f8f; }
.accepted-amendment { border-top: 4px solid #8b5cf6; }
.accepted-amendment .accepted-type { background: #f3e8ff; color: #6b21a8; }
.accepted-config { border-top: 4px solid #007A99; }
.accepted-config .accepted-type { background: #e6f7fb; color: #00627a; }
.accepted-claims { border-top: 4px solid #0f766e; }
.accepted-claims .accepted-type { background: #e7f8f5; color: #0f5f58; }
.accepted-roster { border-top: 4px solid #64748b; }
.accepted-roster .accepted-type { background: #eef2f7; color: #475569; }
.masking-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 11px 14px;
    border-radius: 10px;
    background: #f5f3ff;
    border: 1px solid #ddd6fe;
    color: #4c1d95;
    font-size: 13px;
    margin-top: 10px;
}
@media (max-width: 900px) {
    .accepted-grid { grid-template-columns: repeat(2, minmax(150px, 1fr)); }
}

/* Use Case Cards */
.mode-card-reconcile {
    background-color: #FFFFFF;
    border-left: 5px solid #7A52B3;
    border: 1px solid #E0D7F4;
    border-left-width: 5px;
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
}
.mode-card-negotiate {
    background-color: #FFFFFF;
    border-left: 5px solid #009BBF;
    border: 1px solid #E0D7F4;
    border-left-width: 5px;
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 10px;
}

.success-note {padding:10px 13px; border-radius:10px; background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46;}
.warning-note {padding:10px 13px; border-radius:10px; background:#fffbeb; border:1px solid #fde68a; color:#92400e;}
.advisory-note {padding:10px 13px; border-radius:10px; background:#eff6ff; border:1px solid #bfdbfe; color:#1e40af; font-size:13px;}
.footer {text-align:center; color:#9ca3af; font-size:12px; margin-top:28px;}
/* Accepted File Types */
.accepted-file-types {
    margin-top: 16px;
}

.accepted-file-types-title {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    font-size: 13px;
    font-weight: 600;
    color: #344054;
}

.file-type-icon {
    font-size: 16px;
}

.file-type-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px;
}

.file-type-card {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid #e4e7ec;
    border-radius: 10px;
    background: #f8fafc;
    min-height: 58px;
    box-sizing: border-box;
}

.file-type-card-icon {
    width: 34px;
    height: 34px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 8px;
    background: #eef2ff;
    font-size: 17px;
    flex-shrink: 0;
}

.file-type-card-content {
    display: flex;
    flex-direction: column;
    gap: 3px;
}

.file-type-name {
    font-size: 13px;
    font-weight: 600;
    color: #1d2939;
}

.file-type-formats {
    font-size: 12px;
    color: #667085;
    font-weight: 500;
}

</style>
""", unsafe_allow_html=True)

# ===========================================================================
# 3.3 STRENGTHEN AAVA INTEGRATION VISIBILITY (HEADER)
# ===========================================================================
def get_base64_image(image_path):
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode()

logo_base64 = get_base64_image(ROOT / "assets" / "aava_logo.png") # Adjust path to your file

st.markdown(f'''
<div class="spark-header">
    <img src="data:image/png;base64,{logo_base64}" class="aava-logo" alt="AAVA Logo"/>
    <div>
        <div class="spark-title">SPARK Application</div>
        <div class="spark-subtitle">Provider Contract Intelligence • Powered by AAVA Engine</div>
    </div>
</div>
''', unsafe_allow_html=True)

# Banner Callout for AAVA Capabilities
st.markdown("""
<div class="aava-callout-banner">
    <div style="display: flex; justify-content: space-between; align-items: center;">
        <div>
            <h3 style="margin:0; color:white; font-size:20px;">Run Evaluation using AAVA Platform</h3>
            <p style="margin:6px 0 0 0; opacity:0.92; font-size:14px;">
                Leverage AI-assisted analysis to extract contract parameters, reconcile paid execution, and benchmark pre-signature drafts.
            </p>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ===========================================================================
# 3.1 ADD USER PERSONA & USE CASE INFORMATION
# ===========================================================================
with st.expander("📌 User Personas & Tool Operating Modes (Reconcile vs Negotiate)", expanded=True):
    col_p1, col_p2 = st.columns(2)
    
    with col_p1:
        st.markdown("#### Primary User Personas & End Users")
        st.markdown("""
        * **Commercial Operations Managers:** Ensure execution, configuration, and claims billing strictly align with contracted rates post-execution.
        * **Procurement & Sourcing Leads:** Evaluate new vendor and provider draft agreements against historical market rates to negotiate better terms before signing.
        * **Contract Compliance Auditors:** Detect operational leakage, improper billing setup, and unfulfilled contract terms.
        """)
        
    with col_p2:
        st.markdown("#### Tool Use Cases & Business Value")
        st.markdown("""
        <div class="mode-card-reconcile">
            <strong style="color: #7A52B3; font-size: 15px;">🔄 Reconcile Mode (Post-Execution Audit)</strong><br/>
            Compares <em>signed contracts vs. what was actually configured in systems and paid in claims</em> to detect overbilling, rate mismatches, and configuration errors.
        </div>
        <div class="mode-card-negotiate">
            <strong style="color: #007A99; font-size: 15px;">🤝 Negotiate Mode (Pre-Execution Benchmarking)</strong><br/>
            Evaluates <em>draft contracts vs. historical contract portfolio data + system feasibility rules</em> before signature to guide negotiations and prevent unfeasible terms.
        </div>
        """, unsafe_allow_html=True)

with st.expander("System Configuration", expanded=False):
    st.write(f"**AAVA endpoint:** `{settings.aava_api_base}`")
    st.write(f"**Reconcile workflow (signed PSA):** `{settings.aava_workflow_id}`")
    st.write(f"**Negotiate workflow (draft contract):** `{settings.aava_negotiate_workflow_id}`")
    if settings.aava_api_token:
        st.success("AAVA token configured")
    else:
        st.error("AAVA token is not configured. Set AAVA_API_TOKEN in .env.")

tab_reconcile, tab_negotiate = st.tabs(["📋  Reconcile", "🤝  Negotiate"])


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
    """Classify the legacy package format while accepting multi-format inputs."""
    result = {"psa": None, "amendment": None, "config": None, "claims": None, "roster": None}
    for path in files:
        name = path.name.upper()
        ext = path.suffix.lower()
        category = None

        # Preserve the original filename-prefix contract.
        if name.startswith("PSA_") and ext in {".pdf", ".docx"}:
            category = "psa"
        elif name.startswith("AMENDMENT_") and ext in {".pdf", ".docx"}:
            category = "amendment"
        elif name.startswith("CONFIG_") and ext in {".csv", ".xlsx", ".json"}:
            category = "config"
        elif name.startswith("CLAIMS_") and ext in {".csv", ".xlsx", ".json"}:
            category = "claims"
        elif name.startswith("ROSTER_") and ext in {".csv", ".xlsx", ".json"}:
            category = "roster"
        else:
            # Also accept the supplied multi-format dataset naming convention
            # without changing the visible legacy UI.
            low = name.lower()
            if any(x in low for x in ("participating_provider_agreement", "provider_agreement")) and ext in {".pdf", ".docx"}:
                category = "psa"
            elif "amendment" in low and ext in {".pdf", ".docx"}:
                category = "amendment"
            elif ("fee_schedule" in low or "config" in low) and ext in {".csv", ".xlsx", ".json"}:
                category = "config"
            elif ("provider_roster" in low or "roster" in low) and ext in {".csv", ".xlsx", ".json"}:
                category = "roster"
            elif ("professional_claims" in low or "claims" in low) and ext in {".csv", ".xlsx", ".json"}:
                category = "claims"

        if category:
            if result[category] is not None:
                raise ValueError(f"More than one {category} file found. Please keep one {category.upper()} file in the ZIP.")
            result[category] = path
    return result


def _save_uploaded_file(uploaded, directory: Path, default_name: str) -> Path | None:
    """Persist a Streamlit upload to a temporary directory."""
    if not uploaded:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = Path(uploaded.name).name or default_name
    path = directory / safe_name
    path.write_bytes(uploaded.getvalue())
    return path


def render_reconcile_tab():
    """Reconcile UI: one ZIP package containing the five legacy input roles."""
    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">1</span><span class="step-title">Upload Reconcile Input Package</span>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hint">Upload one ZIP file containing the Contract, Amendment, Config, Claims, and optional Roster files. The ZIP is extracted and each file is classified by filename.</div>',
        unsafe_allow_html=True,
    )

    package_upload = st.file_uploader(
        "Reconcile Input Package (.zip) *",
        type=["zip"],
        key="reconcile_package",
        help="Required: one ZIP package. Keep one file for each supported role using the required filename prefixes.",
    )

    st.markdown("**Package Content — Accepted File Types**")
    st.markdown(
        '<div class="accepted-grid">'
        '<div class="accepted-card accepted-contract"><div class="accepted-label">📄 Contract / PSA</div><span class="accepted-type">PDF · DOCX</span></div>'
        '<div class="accepted-card accepted-amendment"><div class="accepted-label">📝 Amendment</div><span class="accepted-type">PDF · DOCX</span></div>'
        '<div class="accepted-card accepted-config"><div class="accepted-label">⚙️ Config</div><span class="accepted-type">CSV · XLSX · JSON</span></div>'
        '<div class="accepted-card accepted-claims"><div class="accepted-label">🧾 Claims</div><span class="accepted-type">CSV · XLSX · JSON</span></div>'
        '<div class="accepted-card accepted-roster"><div class="accepted-label">👥 Roster <span style="font-weight:500; color:#64748b;">(Optional)</span></div><span class="accepted-type">CSV · XLSX · JSON</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="advisory-note"><strong>🔒 Contract masking:</strong> PSA and Amendment documents are automatically PHI/PII masked before they are sent to the AAVA extraction workflow. Supported contract formats: PDF and DOCX.</div>',
        unsafe_allow_html=True,
    )

    if not package_upload:
        st.markdown('<div class="hint">Required: one Reconcile ZIP package.</div>', unsafe_allow_html=True)
        return

    package_size_mb = len(package_upload.getvalue()) / (1024 * 1024)
    st.markdown(
        f'<div class="success-note">✓ Reconcile package ready &nbsp;•&nbsp; {package_upload.name} &nbsp;•&nbsp; {package_size_mb:.2f} MB</div>',
        unsafe_allow_html=True,
    )

    run = st.button(
        "✨  Run Contract Validation using AAVA",
        type="primary",
        use_container_width=True,
        key="reconcile_run",
    )
    st.markdown('</div>', unsafe_allow_html=True)

    if not run:
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="spark_reconciliation_"))
    extracted_dir = temp_dir / "input"
    masked_dir = temp_dir / "masked"

    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">2</span><span class="step-title">Package & Masking Status</span>', unsafe_allow_html=True)
    status_box = st.empty()
    progress = st.progress(0)
    st.markdown('</div>', unsafe_allow_html=True)

    try:
        status_box.info("Extracting and validating the ZIP package...")
        extracted_files = safe_extract_zip(package_upload, extracted_dir)
        progress.progress(10)

        classified = classify_files(extracted_files)
        required = {"psa": "PSA / Contract", "config": "Config", "claims": "Claims"}
        missing = [label for key, label in required.items() if not classified.get(key)]
        if missing:
            raise ValueError(
                "Missing required file(s) in ZIP: " + ", ".join(missing) + ". "
                "Use the expected filename prefixes (PSA_, CONFIG_, CLAIMS_)."
            )

        # Show the resolved package structure before processing.
        package_rows = []
        for key, label in [
            ("psa", "Contract / PSA"),
            ("amendment", "Amendment"),
            ("config", "Config"),
            ("claims", "Claims"),
            ("roster", "Roster"),
        ]:
            path = classified.get(key)
            package_rows.append({
                "Input": label,
                "File": path.name if path else "Not supplied",
                "Status": "Ready" if path else ("Optional" if key in {"amendment", "roster"} else "Missing"),
            })
        st.dataframe(pd.DataFrame(package_rows), use_container_width=True, hide_index=True)
        progress.progress(20)

        # Create masked copies only for contract documents. Operational extracts
        # remain unchanged because they are not sent through contract extraction.
        psa_path = classified["psa"]
        amendment_path = classified.get("amendment")
        status_box.info("Applying PHI/PII masking to Contract and Amendment...")
        masked_psa = masked_dir / psa_path.name
        _, psa_vault = mask_document_file(psa_path, masked_psa)
        masked_amendment = None
        amendment_vault = {}
        if amendment_path:
            masked_amendment = masked_dir / amendment_path.name
            _, amendment_vault = mask_document_file(amendment_path, masked_amendment)
        progress.progress(35)

        mask_rows = [
            {"Document": "Contract / PSA", "Status": "Masked before AAVA", "Items masked": len(psa_vault)},
        ]
        if amendment_path:
            mask_rows.append({"Document": "Amendment", "Status": "Masked before AAVA", "Items masked": len(amendment_vault)})
        else:
            mask_rows.append({"Document": "Amendment", "Status": "Not supplied", "Items masked": 0})
        st.markdown('<div class="success-note">✓ Contract documents are masked before AAVA processing. Original uploaded files are not used for extraction.</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(mask_rows), use_container_width=True, hide_index=True)

        config_path = classified["config"]
        claims_path = classified["claims"]
        roster_path = classified.get("roster")

        status_box.info("Validating Config and Claims extracts...")
        config_df = load_config(str(config_path))
        claims_df = load_claims(str(claims_path))
        progress.progress(50)

        if roster_path:
            try:
                _ = load_and_validate_csv(str(roster_path)) if roster_path.suffix.lower() == ".csv" else None
            except Exception as roster_exc:
                st.warning(f"Provider roster could not be validated automatically: {roster_exc}")

        status_box.info("Sending masked Contract + Amendment to AAVA workflow...")
        progress.progress(55)
        terms = extract_via_aava(
            upload_id=temp_dir.name,
            psa_path=str(masked_psa),
            amendment_path=str(masked_amendment) if masked_amendment else None,
            user_email="",
        )
        progress.progress(80)

        if not terms:
            raise AavaExtractionError(
                "AAVA workflow completed but returned no contract terms. "
                "Check the AAVA workflow output/document processing result."
            )

        status_box.info("AAVA output received. Validating against Config Extract and Claims Pull...")
        results_df = reconcile(terms, config_df, claims_df)
        progress.progress(100)
        status_box.success(f"Completed — {len(results_df)} claim(s) processed.")

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
            status_filter = st.multiselect(
                "Status filter",
                statuses,
                default=statuses,
                key="reconcile_status_filter",
            )
            filtered = df[df["status"].isin(status_filter)]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Flagged", int((df["status"] == "flagged").sum()))
            c3.metric("Needs Review", int((df["status"] == "needs_review").sum()))
            c4.metric("Clean", int((df["status"] == "clean").sum()))
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            st.download_button(
                "Download Validation Results CSV",
                filtered.to_csv(index=False).encode("utf-8"),
                "validation_results.csv",
                "text/csv",
                use_container_width=True,
                key="reconcile_download",
            )
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
    for i, term in enumerate(terms):
        if not term.get("term_id"):
            term["term_id"] = f"AAVA-{i+1:03d}"
    return terms


def render_negotiate_tab():
    st.markdown('<div class="advisory-note">Advisory only — every recommendation here is a starting point for the contracting committee, not an automated decision. Nothing in this tab writes to any external contracting system.</div>', unsafe_allow_html=True)
    st.write("")

    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">1</span><span class="step-title">Upload Draft Contract</span>', unsafe_allow_html=True)
    st.markdown('<div class="hint">Upload one unsigned draft contract in PDF or DOCX format. Terms are benchmarked against the historical portfolio and checked against system feasibility rules below.</div>', unsafe_allow_html=True)
    draft_pdf = st.file_uploader("Draft contract (unsigned)", type=["pdf", "docx"], label_visibility="collapsed", key="negotiate_draft")

    with st.expander("Reference data (historical portfolio + config capability rules)", expanded=False):
        st.caption("Defaults to the standing reference dataset in data/phase2_reference/. Upload your own to override for this run only.")
        c1, c2 = st.columns(2)
        with c1:
            portfolio_upload = st.file_uploader("Historical portfolio CSV (optional override)", type=["csv"], key="negotiate_portfolio")
            st.caption(f"Default: `{DEFAULT_PORTFOLIO_PATH.relative_to(ROOT)}`")
        with c2:
            rules_upload = st.file_uploader("Config capability rules JSON (optional override)", type=["json"], key="negotiate_rules")
            st.caption(f"Default: `{DEFAULT_RULES_PATH.relative_to(ROOT)}`")

    st.markdown('<div class="accepted-file-types"><div class="accepted-file-types-title"><span class="file-type-icon">📄</span><span>Accepted File Types</span></div>  <div class="file-type-grid"> <div class="file-type-card contract"><div class="file-type-card-icon">📄</div><div class="file-type-card-content"><div class="file-type-name">Draft Contract</div><div class="file-type-formats">PDF · DOCX</div></div></div> </div></div>', unsafe_allow_html=True)

    run = st.button("🤝  Run Evaluation using AAVA", type="primary", disabled=not draft_pdf, use_container_width=True, key="negotiate_run")
    st.markdown('</div>', unsafe_allow_html=True)

    if not run:
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="spark_negotiate_"))

    # ===========================================================================
    # 4. WORKFLOW STATUS SECTION (CORRECTED INSIDE CONTAINER)
    # ===========================================================================
    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown('<span class="step-num">2</span><span class="step-title">Masking & Workflow Status</span>', unsafe_allow_html=True)
    st.markdown(
        '<div class="masking-banner"><strong>🔒 Data Protection:</strong> The draft contract is automatically PHI/PII masked before it is sent to the AAVA negotiation workflow.</div>',
        unsafe_allow_html=True,
    )
    status_box = st.empty()
    progress = st.progress(0)
    st.markdown('</div>', unsafe_allow_html=True)

    try:
        draft_path = temp_dir / f"draft_contract{Path(draft_pdf.name).suffix.lower()}"
        draft_path.write_bytes(draft_pdf.getvalue())
        masked_draft_path = temp_dir / f"masked_{Path(draft_pdf.name).name}"

        status_box.info("Applying PHI/PII masking to the draft contract...")
        _, draft_vault = mask_document_file(draft_path, masked_draft_path)
        st.markdown(
            f'<div class="success-note">✓ Draft contract masked before AAVA &nbsp;•&nbsp; {len(draft_vault)} sensitive item(s) masked</div>',
            unsafe_allow_html=True,
        )
        progress.progress(10)

        status_box.info("Loading reference data (historical portfolio + config rules)...")
        portfolio_df = _load_portfolio_df(portfolio_upload)
        config_rules = _load_config_rules(rules_upload)
        progress.progress(15)

        status_box.info("Sending masked draft contract to AAVA negotiate workflow...")
        progress.progress(20)
        draft_terms = extract_draft_terms_via_aava(
            upload_id=temp_dir.name,
            draft_contract_path=str(masked_draft_path),
        )
        progress.progress(70)

        if not draft_terms:
            raise AavaExtractionError("AAVA negotiate workflow completed but returned no draft terms. Check the AAVA workflow output/document processing result.")
        draft_terms = _ensure_term_ids(draft_terms)

        status_box.info("Benchmarking against historical portfolio and checking feasibility...")
        results_df = evaluate_draft_terms(draft_terms, portfolio_df, config_rules)
        progress.progress(100)
        status_box.success(f"Completed — {len(results_df)} draft term(s) evaluated.")

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

st.markdown('<div class="footer">SPARK • Provider Contract Intelligence Demo • Powered by AAVA</div>', unsafe_allow_html=True)