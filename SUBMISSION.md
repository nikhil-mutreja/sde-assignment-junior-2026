# Submission

## Candidate
- **Name:** Nikhil Mutreja
- **Assignment chosen:** A (Data: Interaction Ledger & Reporting Pipeline)
- **Time spent (honest estimate):** ~6 hours

## How to run
```bash
# 1. Setup virtual environment and dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. (Optional) Configure environment variables or use defaults
cp .env.example .env

# 3. Generate sample data if needed (small scale seed 42)
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# 4. Ingest drops incrementally
python -m solution.cli ingest data/drop_1
python -m solution.cli ingest data/drop_2

# 5. Generate client and operational reports
python -m solution.cli report --out reports/

# 6. Display pipeline stats table
python -m solution.cli stats

# 7. Run full test suite with coverage
pytest -v

# 8. Run Ruff linting and formatting verification
ruff check .
ruff format --check .
```

## Design

The solution is structured as a modular, decoupled data pipeline with clear boundaries across ingestion, persistence, security, service resolution, and reporting.

```mermaid
flowchart TD
    subgraph Ingestion
        A1[Lead CSV Drops] --> B1[Header Normalisation & Alias Mapping]
        B1 --> C1[Schema Validation in Order]
        C1 -->|Invalid Row| D1[(RejectedLeads Table)]
        C1 -->|Valid Row| E1[Duplicate Merging & Permanent Batch ID]
        E1 --> F1[(Leads Table: PII Encrypted + HMAC Hash)]

        A2[Call Event JSONL Drops] --> B2[Precedence: Malformed -> Dedupe]
        B2 --> C2[Append-Only Ledger Persistence]
        C2 --> D2[(InteractionEvents Table)]
    end

    subgraph Service Layer
        F1 -.-> G[Cooperative Orphan Resolution Engine]
        D2 -.-> G
        G -->|Matches (workflow_id, phone_hash)| H[Link Event to Lead]
        G -->|Matches call_id for Overrides| H
    end

    subgraph Reporting Engine
        H --> I[Call Facts Aggregator]
        I --> J[Effective Disposition Logic]
        J --> K[Lead Bucket Classifier]
        K --> L1[batch_summary.csv]
        K --> L2[daily_overview.csv]
        K --> L3[lead_report.csv]
        D1 --> L4[rejections.csv]
        D2 --> L5[data_quality.json]
    end
```

### Relational Database Schema & Ledger Architecture

The database is built on SQLite (with complete support for PostgreSQL via SQLAlchemy 2.x declarative models). The schema consists of four core relational entities:

1. **`ingested_files`**: Tracks every ingested file by absolute path, file type (`leads` or `events`), and content SHA-256 hash. Stores exact file-level counters (`lead_rows_total`, `duplicate_lead_rows_merged`, `field_warnings`, `events_total`, `malformed_events`, `duplicate_events_dropped`). This guarantees strict idempotency on file replays without inflating quality metrics.
2. **`leads`**: Stores normalized customer leads. Identity is `(workflow_id, phone_hash)`.
   - `workflow_id`: Workflow identifier (`motor_renewal`, `policy_feedback`).
   - `phone_hash`: Deterministic HMAC-SHA256 hex digest of the canonical phone (`+91XXXXXXXXXX`). Indexed with a unique constraint `(workflow_id, phone_hash)`.
   - `batch_id`: First valid batch stem (e.g. `batch_001`), permanently established.
   - `external_ref`: Value from the schema field marked `"reference": true` (`policy_number` or `order_id`).
   - `phone_last4`: Last 4 digits of the canonical phone (e.g. `5036`) for instant masked reporting (`+91******5036`).
   - `encrypted_customer_name`: AES-256-GCM ciphertext + 12-byte random nonce.
   - `encrypted_canonical_phone`: AES-256-GCM ciphertext + 12-byte random nonce.
   - `workflow_attributes`: JSON document storing non-PII fields (`policy_expiry`, `premium_amount`, `preferred_language`, `product`, `order_date`).
3. **`rejected_leads`**: Append-only log of rejected rows storing `(workflow_id, batch_id, row_number, reason)`. Never contains customer PII.
4. **`interaction_events`**: Append-only interaction ledger.
   - `event_id`: Globally unique deduplication key (indexed).
   - `event_type`: Event kind (`call.initiated`, `call.connected`, `call.telephony_failed`, `call.ended`, `disposition.set`, `disposition.override`).
   - `workflow_id`, `call_id`: Relational identifiers (indexed).
   - `phone_hash`: Deterministic HMAC hash if phone is present in event.
   - `lead_id`: Foreign key to `leads.id`, populated upon resolution.
   - `occurred_at`: Stored in standard UTC.
   - `encrypted_phone`: AES-256-GCM encrypted raw/canonical phone.
   - `payload_json`: Non-PII payload data.
   - `is_linked`: Boolean flag indicating whether the event is linked to a lead.
   - `is_unknown_disposition`: Flag indicating if a linked disposition event has a code not in the disposition catalogue.

### Security: PII Protection and Deterministic Phone Matching

- **AES-256-GCM Encryption at Rest**: Sensitive fields (`customer_name`, `phone`, and any schema field marked `pii: true`) are encrypted using AES-256-GCM with a freshly generated 12-byte cryptographic nonce (`os.urandom(12)`) per encryption call. Ciphertext and authentication tag are packed with the nonce as `base64(nonce || ciphertext || tag)`. Plaintext names and phone numbers never exist in database columns.
- **HMAC-SHA256 Deterministic Matching**: Randomized encryption prevents direct equality lookups in SQL. To match events to leads without decrypting table scans, phones are canonicalized to `+91XXXXXXXXXX`, hashed via HMAC-SHA256 using an isolated secret key (`PHONE_HMAC_KEY`), and stored in `phone_hash`. Matching is done on `(workflow_id, phone_hash)`.
- **Rejection Confidentiality**: `rejections.csv` only contains `workflow_id,batch_id,row_number,reason`. No customer names or phone numbers are ever written to rejection files or stdout logs.

### Idempotency & Incremental Ingestion

1. **File-Level Content Hashing**: Before processing any file, its SHA-256 digest is checked against `ingested_files`. Re-running `ingest data/drop_1` detects already ingested files and skips them without mutating database state or inflating counters.
2. **Duplicate Merging**: Duplicate lead records within a batch or across drops match `(workflow_id, phone_hash)`. The initial `batch_id` is permanently retained. New non-null fields overwrite existing values, while schema defaults apply to remaining null fields.
3. **Cooperative Pending Event Resolution**: Events arriving before their leads (e.g. drop 1 events for drop 2 batch 003 leads) are safely stored in the append-only ledger with `is_linked = False`. When drop 2 is ingested, `resolve_pending_events()` performs fixed-point reconciliation:
   - Matches unlinked events to newly added leads via `(workflow_id, phone_hash)`.
   - Matches unlinked call events (including `disposition.override` which carries no phone) via `call_id` to sibling events already linked.
   - Re-checks unknown disposition codes on newly linked events.
   - Ensures `orphan_events` in `data_quality.json` reflects the true final unresolved state.

### Effective Disposition Logic

For any call:
- Evaluates only linked, valid disposition events (`is_linked == True` and `is_unknown_disposition == False`).
- Overrides never update or delete original events (preserving ledger immutability).
- If one or more `disposition.override` events exist, selects the latest override by `occurred_at`.
- If no overrides exist, selects the latest `disposition.set` by `occurred_at`.
- If neither exists, disposition is null.
- **Deterministic Tie-Breaking**: If multiple overrides or sets share identical `occurred_at` timestamps, tie-break by `event_id` ascending.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| **HMAC-SHA256 with separate secret key** | Allows O(1) indexed lookups for lead-event matching while keeping PII encrypted with randomized AES-GCM. An isolated key prevents correlation attacks if an encryption key is rotated. | Blind indexing or decrypting all leads into memory on every batch. |
| **Fixed-point iterative orphan resolution** | Overrides arrive with `call_id` only. If an override arrives before `call.initiated`, and `call.initiated` arrives before the lead, linking requires transitive resolution: Lead -> `call.initiated` -> `disposition.override`. | Storing overrides in a separate quarantine table; discarded because append-only ledger is cleaner when all events live in one unified table. |
| **File-level metrics summation in `data_quality.json`** | Storing file-level metrics in `ingested_files` guarantees that re-running `ingest` never inflates `events_total`, `malformed_events`, or `duplicate_lead_rows_merged`. Live orphan counts and rejections are derived directly from database queries. | Incrementing global counters; prone to race conditions and double-counting on replay. |
| **Deterministic tie-breaking on identical timestamps** | When supervisors submit simultaneous overrides or data generators emit identical seconds, sorting by `(occurred_at, event_id)` ensures 100% deterministic, reproducible effective dispositions. | Ingestion order (nondeterministic across systems) or random pick. |
| **In-memory cache for intra-batch duplicates** | Generating synthetic data produces ~3% duplicates within the same batch file. In-memory caching per workflow prevents SQLite `UNIQUE constraint failed` before transactions flush. | Flushing to DB on every single row (10x slower). |

## Failure modes considered

- **Malformed JSON lines or truncated rows**: Caught at the JSON parsing and schema validation stage; counted in `malformed_events` and skipped without aborting file ingestion.
- **Out-of-order event delivery**: An override arriving hours before `call.initiated` or before the lead is ingested is safely buffered in the ledger and resolved transitively upon lead arrival.
- **Database crash or interrupted ingestion**: Operations run within transactional sessions (`session.commit()` / `session.rollback()`), preventing partially committed batch files.
- **Duplicate files / replays**: File SHA-256 verification detects duplicate runs and safely skips them with zero side effects.
- **Invalid optional values**: Impossible calendar dates (e.g. `31/02/2026`), negative currency values, or unknown enum values convert to null and increment `field_warnings` by 1 without rejecting valid leads.
- **Timezone skew**: All incoming timestamps are normalized immediately to UTC internally. All reports and output timestamps are strictly converted to Asia/Kolkata (IST), independent of local host OS timezone.
- **Unlinkable calls**: Calls placed to phone numbers never uploaded remain in the ledger as orphans (`is_linked = False`) and are strictly excluded from client-facing reports.

## Testing

Comprehensive test suite covering 72 unit and integration tests across 12 test modules:
- `test_phone_normalisation.py`: 6 valid format variations, 11 invalid phone conditions, and phone masking.
- `test_date_normalisation.py`: 4 date formats, leap year validation, impossible date rejections, naive IST parsing, and midnight boundary handling.
- `test_lead_validation.py`: Header alias mapping, missing required fields, schema order precedence, currency/decimal parsing, and enum case-insensitivity.
- `test_duplicate_merging.py`: Permanent `batch_id` retention, non-null field overwrite, and default value application.
- `test_event_ingestion.py`: Malformed JSON handling, duplicate event dropping, and append-only ledger immutability.
- `test_orphan_resolution.py`: Deferred orphan resolution across drops, phone matching, and override resolution via `call_id`.
- `test_effective_disposition.py`: Single/multiple sets, single/multiple overrides, out-of-order overrides, unknown dispositions, and tie-breaking.
- `test_lead_buckets.py`: All 4 lead buckets and their exact precedence hierarchy (`yet_to_reach_out` -> `completed` -> `in_progress_telephony_failed` -> `in_progress_dialed`).
- `test_reporting.py`: Exact CSV headers, decimal formatting (`0.0000`), masked phone numbers, ISO-8601 IST timestamps, and JSON quality contract.
- `test_integration.py`: End-to-end multi-drop lifecycle (drop 1 leads & events, cross-drop orphan resolution in drop 2, override propagation, and full report generation).
- `test_idempotency.py`: Re-ingesting drops produces identical database state and counters without inflation.
- `test_security.py`: Verifies AES-256-GCM ciphertext nonces, HMAC determinism, and absence of plaintext PII in database columns and rejection logs.

## Results

### Execution Timings (seed=42, scale=small: 2,000 leads, 8,745 event lines)
- **Drop 1 Ingestion**: 4.1s
- **Drop 2 Ingestion**: 3.6s
- **Report Generation (all 5 reports)**: 0.8s
- **Full Test Suite (72 tests)**: 0.29s

### Data Quality Summary (Seed 42)
```json
{
  "lead_rows_total": 2057,
  "lead_rows_rejected": 108,
  "rejections_by_reason": {
    "invalid_phone": 42,
    "missing_required:customer_name": 25,
    "missing_required:order_id": 13,
    "missing_required:phone": 14,
    "missing_required:policy_number": 14
  },
  "duplicate_lead_rows_merged": 57,
  "field_warnings": 122,
  "events_total": 8745,
  "malformed_events": 8,
  "duplicate_events_dropped": 237,
  "orphan_events": 462,
  "unknown_disposition_events": 18
}
```

## If I had another day
1. **SQL-Based Aggregation**: Move lead bucket classification and daily overview aggregation into SQLite/PostgreSQL window functions and CTEs to stream 100k+ leads with constant memory overhead.
2. **Key Rotation Architecture**: Implement envelope encryption with key-version identifiers (`v1:nonce:ciphertext`) to allow zero-downtime PII key rotation.
3. **Partitioned Ingestion Workers**: Use Celery/Temporal workers with workflow partitioning so that multiple drops or large batch files ingest concurrently without database lock contention.
4. **Data Contract Schema Registry**: Publish JSON schema validation rules via a centralized contract registry with automated CI contract-testing against provider payloads.
