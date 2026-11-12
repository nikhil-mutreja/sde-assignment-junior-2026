# Solution: Interaction Ledger & Reporting Pipeline (Assignment A)

This package implements the end-to-end data pipeline for Agentic Universe's Assignment A.

## Architectural Overview

The package is organized into decoupled layers:

- **`config.py`**: Environment variables, default paths, timezones (`UTC` and `Asia/Kolkata` IST), and safe development encryption keys.
- **`security.py`**: Field-level encryption using **AES-256-GCM** with random 12-byte nonces; deterministic phone matching using **HMAC-SHA256**.
- **`models.py`**: SQLAlchemy 2.x relational database schema (`ingested_files`, `leads`, `rejected_leads`, `interaction_events`).
- **`database.py`**: Database engine and transactional session management.
- **`schemas.py`**: Pydantic schema loaders for workflow definitions and the disposition catalogue.
- **`ingestion/`**:
  - `normalisation.py`: Canonical phone formatting (`+91XXXXXXXXXX`), 4-format day-first date parsing, currency and decimal parsing, and lowercase enum validation.
  - `validation.py`: Schema-ordered field walk rejecting on the first failure (`missing_required:<field>` or `invalid_phone`).
  - `leads.py`: Lead CSV ingestion with header alias mapping, permanent `batch_id` assignment, duplicate lead merging, and default backfill.
  - `events.py`: JSONL parsing adhering strictly to event precedence (Malformed $\to$ Duplicate $\to$ Orphan $\to$ Unknown Disposition) with ledger persistence.
- **`services/`**:
  - `ledger.py`: Cooperative, iterative fixed-point orphan resolution engine and effective disposition logic.
  - `data_quality.py`: Calculation of the 10 data quality counters across drops without duplicate inflation.
- **`reporting/`**:
  - `aggregator.py`: Derives call facts, lead facts, and bucket classification (`yet_to_reach_out` $\to$ `completed` $\to$ `in_progress_telephony_failed` $\to$ `in_progress_dialed`).
  - `reports.py`: Exporters generating all five CSV and JSON report contracts.
- **`cli.py`**: Command-line interface powered by Typer.

## CLI Usage

```bash
# Initialize database
python -m solution.cli init-db

# Ingest drops
python -m solution.cli ingest data/drop_1
python -m solution.cli ingest data/drop_2

# Generate reports
python -m solution.cli report --out reports/

# Display statistics
python -m solution.cli stats
```
