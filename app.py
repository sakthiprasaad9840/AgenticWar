import io
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.config import settings
from lib.aava_client import extract_via_aava, AavaExtractionError
from lib.validation import load_and_validate_csv, EXPECTED_CONFIG_COLUMNS, EXPECTED_CLAIMS_COLUMNS
from lib.join_engine import reconcile

st.set_page_config(page_title="Spark | Contract Reconciliation", page_icon="✨", layout="wide", initial_sidebar_state="collapsed")

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
.footer {text-align:center; color:#9ca3af; font-size:12px; margin-top:28px;}
</style>
""", unsafe_allow_html=True)

st.markdown('''<div class="spark-header"><div class="spark-logo">✦</div><div><div class="spark-title">SPARK</div><div class="spark-subtitle">Contract Reconciliation • Contract → AAVA → Validation</div></div></div>''', unsafe_allow_html=True)
st.caption("A simple validation workspace for extracting contract terms and comparing them with configured and paid claim data.")

with st.expander("Configuration", expanded=False):
    st.write(f"**AAVA workflow:** `{settings.aava_workflow_id}`")
    st.write(f"**AAVA endpoint:** `{settings.aava_api_base}`")
    if settings.aava_api_token:
        st.success("AAVA token configured")
    else:
        st.error("AAVA token is not configured. Set AAVA_API_TOKEN in .env.")

# ---------------------------------------------------------------------------
# Input package parsing
# ---------------------------------------------------------------------------
REQUIRED = {"psa": "PSA_", "config": "CONFIG_", "claims": "CLAIMS_"}
OPTIONAL = {"amendment": "AMENDMENT_", "roster": "ROSTER_"}


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


st.markdown('<div class="step-card">', unsafe_allow_html=True)
st.markdown('<span class="step-num">1</span><span class="step-title">Upload Input Package</span>', unsafe_allow_html=True)
st.markdown('<div class="hint">Upload one ZIP containing the contract, extracts, and optional amendment/roster. The app identifies each file from its filename prefix.</div>', unsafe_allow_html=True)
package = st.file_uploader("Input ZIP", type=["zip"], label_visibility="collapsed")
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
run = st.button("✨  Run Contract Validation", type="primary", disabled=not ready, use_container_width=True)
st.markdown('</div>', unsafe_allow_html=True)

if run:
    temp_dir = Path(tempfile.mkdtemp(prefix="spark_reconciliation_"))
    status_box = st.empty()
    progress = st.progress(0)
    try:
        # Re-extract into the run directory so all processing uses a stable path.
        run_files = safe_extract_zip(package, temp_dir)
        files = classify_files(run_files)
        config_path = files["config"]
        claims_path = files["claims"]
        contract_path = temp_dir / "psa_exhibit.pdf"
        contract_path.write_bytes(files["psa"].read_bytes())
        amendment_path = None
        if files["amendment"]:
            amendment_path = temp_dir / "amendment.pdf"
            amendment_path.write_bytes(files["amendment"].read_bytes())

        st.markdown('<div class="step-card">', unsafe_allow_html=True)
        st.markdown('<span class="step-num">2</span><span class="step-title">Workflow Status</span>', unsafe_allow_html=True)
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
            status_filter = st.multiselect("Status filter", statuses, default=statuses)
            filtered = df[df["status"].isin(status_filter)]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", len(df))
            c2.metric("Flagged", int((df["status"] == "flagged").sum()))
            c3.metric("Needs Review", int((df["status"] == "needs_review").sum()))
            c4.metric("Clean", int((df["status"] == "clean").sum()))
            st.dataframe(filtered, use_container_width=True, hide_index=True)
            st.download_button("Download Validation Results CSV", filtered.to_csv(index=False).encode("utf-8"), "validation_results.csv", "text/csv", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    except Exception as exc:
        progress.progress(0)
        status_box.error("Processing failed")
        st.error(str(exc))
        with st.expander("Technical details"):
            st.exception(exc)

st.markdown('<div class="footer">SPARK • Contract Reconciliation Demo • One Streamlit application</div>', unsafe_allow_html=True)
