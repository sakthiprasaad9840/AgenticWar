# Contract Reconciliation + Negotiate — Single Streamlit App

This version does **not** require a separate FastAPI backend.

## Two tabs, two agents

**📋 Reconcile (Phase 1)** — signed contracts vs. what was actually configured and paid.
1. Upload Contract / PSA PDF.
2. Upload Amendment PDF (optional).
3. Upload Config Extract CSV.
4. Upload Claims Pull CSV.
5. Click **Run Contract Validation**.
6. The app sends Contract + Amendment to the AAVA reconcile workflow (`21427`), polls until complete, then validates the returned terms against Config Extract + Claims Pull.
7. The final validation table and CSV download are shown in the browser.

**🤝 Negotiate (Phase 2)** — draft/unsigned contracts vs. historical portfolio + system feasibility, before signature.
1. Upload a draft contract PDF.
2. (Optional) override the standing historical portfolio CSV / config capability rules JSON — defaults live in `data/phase2_reference/`.
3. Click **Run Evaluation**.
4. The app sends the draft contract to the AAVA negotiate workflow (`21656`), polls until complete, then benchmarks the proposed terms against the historical portfolio and checks feasibility against the config capability rules.
5. Results are shown with an **Accept / Negotiate / Modify / Insufficient data** recommendation (advisory only), full reasoning text, and a per-term detail view where a committee decision can be recorded locally for the session.

Both tabs share the same AAVA client, poll/parse plumbing, and "never infer, route to review" discipline.

## Folder structure

```text
contract_reconciliation_simple/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
├── data/
│   └── phase2_reference/
│       ├── historical_portfolio.csv
│       └── config_capability_rules.json
└── lib/
    ├── __init__.py
    ├── config.py
    ├── aava_client.py
    ├── validation.py
    ├── join_engine.py
    └── benchmark_engine.py
```

## Run

### 1. Create and activate a virtual environment

Windows:

```powershell
python -m venv venv
venv\Scripts\activate
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

### 3. Configure AAVA

Copy `.env.example` to `.env` and set your real service-account token:

```text
AAVA_API_TOKEN=your_real_token
```

Do not commit `.env` or share the token.

### 4. Start the application

From the project root:

```powershell
streamlit run app.py
```

Open the Streamlit URL shown in the terminal, normally:

```text
http://localhost:8501
```

## Input CSV columns

### Config Extract (Reconcile tab)

Required columns:

```text
tin,cpt_code,product,effective_date,end_date,configured_rate
```

### Claims Pull (Reconcile tab)

Required columns:

```text
claim_id,tin,cpt_code,product,date_of_service,time_of_service,place_of_service,billed_amount,paid_amount
```

### Historical Portfolio (Negotiate tab, standing reference or override)

Required columns:

```text
tin,cpt_code,product,specialty,region,contract_allowed_amount,effective_date
```

### Config Capability Rules (Negotiate tab, standing reference or override)

JSON, e.g.:

```json
{
  "max_modifiers_per_rate": 1,
  "supported_modifier_types": ["after_hours_uplift", "telehealth_uplift"],
  "unsupported_modifier_types": ["weekend_uplift"]
}
```

## Architecture

```text
Browser
  |
  v
Streamlit app.py
  |
  +-- Reconcile tab -------------------------------------------
  |     Contract + Amendment --> AAVA workflow 21427 --> Poll --> Contract Terms
  |     Config Extract + Claims Pull
  |     --> Join/Reconciliation Engine --> Validation Results
  |
  +-- Negotiate tab (Phase 2) -----------------------------------
        Draft Contract --> AAVA workflow 21656 --> Poll --> Draft Terms
        Historical Portfolio + Config Capability Rules
        --> Benchmark + Feasibility + Recommendation --> Evaluation Results
```

