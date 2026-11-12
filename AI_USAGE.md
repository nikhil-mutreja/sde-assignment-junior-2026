# AI Usage Disclosure

## Tools used
- Claude 3.5 Sonnet / Antigravity Assistant

## What I used them for
- Generating initial boilerplate for Pydantic models and SQLAlchemy 2.0 declarative tables.
- Drafting regex patterns for decimal and date parsing against Indian currency prefixes and month name abbreviations.
- Scaffolding unit test fixtures across different phone number formats and date boundaries.
- Formatting the initial draft of the architecture Mermaid diagram.

## What I wrote or decided myself
- **Core Architecture & Relational Data Model**: Designed the append-only ledger schema, the `(workflow_id, phone_hash)` unique identity model, and the file-level content hash tracking mechanism in `ingested_files`.
- **Transitive Fixed-Point Orphan Resolution**: Recognized that `disposition.override` contains no phone number, requiring a two-phase transitive resolution engine (matching leads via phone hash, then matching overrides via call_id) that iterates until convergence across drops.
- **Security & PII Segregation Strategy**: Decided on randomized AES-256-GCM encryption with 12-byte nonces combined with an isolated HMAC-SHA256 deterministic key for indexing and linking, preventing correlation attacks while allowing O(1) matching.
- **Idempotency Invariant Design**: Ensured that re-running `ingest` on identical drops never mutates database state, never inflates event counters, and produces byte-identical reports.
- **Lead Duplicate Merging Semantics**: Enforced permanent `batch_id` establishment on first write, non-null field overwrite, and default value backfill on null attributes.

## Where the AI was wrong or unhelpful
1. **Intra-Batch Duplicate Merging Collision**:
   The AI's initial ingestion loop queried `session.scalar()` on each row without accounting for duplicate rows arriving in the same uncommitted batch file (synthetic generator produces ~3% duplicates). Because newly created lead entities sat unflushed in SQLAlchemy's identity map, `session.scalar()` returned `None`, attempting a second `INSERT` with the same `(workflow_id, phone_hash)` and throwing a SQLite `UNIQUE constraint failed` error on flush. I diagnosed the failure and introduced an in-memory workflow cache `leads_cache` to resolve intra-batch duplicates before hitting database constraints.
2. **Session Autoflush and Orphan Query Isolation**:
   In testing out-of-order overrides, the AI called `resolve_pending_events()` before flushing pending newly added events in the session. Because `autoflush=False` was configured on the test session factory, queries for sibling call events executed against the database could not see unflushed in-memory events, causing override linking to fail. I resolved this by adding an explicit `session.flush()` at the start of the resolution pass.
3. **CSV Quoting with Currency Commas**:
   In test fixtures, the AI generated raw CSV lines containing unquoted amounts like `₹12,000`, causing standard Python `csv.reader` to split on the comma and misalign the remaining columns. I identified the issue and wrapped currency amounts in quotes.
4. **Rich Terminal Emoji Parsing**:
   When displaying the data quality statistics table via Rich, `missing_required:phone` was automatically rendered as `missing_required☎` because `:phone:` matched Rich's default emoji shortcode parser. I fixed this by disabling markup in the console table print.

## How I verified AI-generated code
- **72 Unit and Integration Tests**: Ran comprehensive pytest suites covering edge cases in phone normalisation, date parsing, duplicate merging, effective dispositions, lead buckets, idempotency, multi-drop integration, and security.
- **Ruff Strict Verification**: Verified all code with `ruff check .` and `ruff format --check .`.
- **End-to-End Multi-Drop Ingestion**: Executed complete runs on small-scale datasets with both seed 42 and seed 99, inspecting the resulting CSV reports and verifying row counts, decimal formatting, and sorting.
- **Security Inspections**: Inspected SQLite database tables directly to confirm that customer names and phone numbers were encrypted and that no plaintext PII leaked into `rejections.csv` or logs.
