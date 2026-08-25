# Simple Contract Reconciliation — Single Streamlit App

This version does **not** require a separate FastAPI backend.

## What it does

1. Upload Contract / PSA PDF.
2. Upload Amendment PDF (optional).
3. Upload Config Extract CSV.
4. Upload Claims Pull CSV.
5. Click **Run Validation**.
6. The Streamlit app sends Contract + Amendment to the AAVA workflow.
7. The app polls AAVA until the workflow completes.
8. The returned contract terms are validated against Config Extract + Claims Pull.
9. The final validation table and CSV download are shown in the browser.

## Folder structure

```text
contract_reconciliation_simple/
├── app.py
├── requirements.txt
├── .env.example
├── README.md
└── lib/
    ├── __init__.py
    ├── config.py
    ├── aava_client.py
    ├── validation.py
    └── join_engine.py
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

### Config Extract

Required columns:

```text
tin,cpt_code,product,effective_date,end_date,configured_rate
```

### Claims Pull

Required columns:

```text
claim_id,tin,cpt_code,product,date_of_service,time_of_service,place_of_service,billed_amount,paid_amount
```

## Architecture

```text
Browser
  |
  v
Streamlit app.py
  |
  +--> Contract + Amendment --> AAVA workflow --> Poll --> Contract Terms
  |
  +--> Config Extract + Claims Pull
  |
  v
Reconciliation Engine
  |
  v
Validation Results
```
