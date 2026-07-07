# Architectural & Design Decisions (`DECISIONS.md`)

This document records the core architectural patterns, extraction strategies, assumptions, and product questions resolved during the development of the ScopeTrace platform.

---

## 1. Architectural Patterns & Strategy Selection

### A. Decoupled Service-Layer Architecture
- **Decision**: Avoid writing business rules (parsers, validators, unit normalizers) in Django views or model methods. Instead, isolate them in the `services/` layer.
- **Rationale**: Keeps views thin (only handling HTTP routing, inputs, and serializations) and models clean. This makes the codebase extremely testable using isolated mocks and permits swapping out components without modifying DB constraints.

### B. The Ingestion Strategy Pattern
- **Decision**: Define a base interface `BaseParser` and implement dedicated strategy classes (`SAPFuelParser`, `UtilityElectricityParser`, `TravelParser`) that are loaded dynamically based on the `DataSource` category.
- **Rationale**: Isolates parser-specific concerns. If SAP CSV schemas change or the utility provider updates their logs, we only alter a single parser strategy object, leaving the orchestrating `IngestionService` completely untouched.

### C. Atomic Database Transactions
- **Decision**: Wrap the entire ingestion sequence and the record approval step inside Django `transaction.atomic()` blocks.
- **Rationale**: Protects data integrity. If a file contains a corrupt schema mid-parse, the database rolls back completely, ensuring no partial/corrupted batches are written.

---

## 2. Inbound Data Extraction Decisions

### A. Semicolon and European Locales (SAP)
- **Decision**: The SAP parser automatically handles semicolon separators (`;`) and parses German decimal formatting (e.g. converting `1.200,50` to float `1200.5`).
- **Rationale**: Mimics real-world SAP ERP exports where regional European configurations use comma decimal separators and dot thousands separators.

### B. Scaled Utility Power (Utility)
- **Decision**: The Utility parser extracts billing period date ranges and scales values from MWh to kWh automatically (multiplying by $1000.0$).
- **Rationale**: Utility invoices are often processed in Megawatts (MWh) but carbon emission calculators globally require base Kilowatts (kWh) for carbon factors.

### C. Haversine Air Distance Estimation (Travel)
- **Decision**: When travel logs omit the distance parameter, the Travel parser references IATA coordinates and computes the distance in kilometers using the **Haversine formula**.
- **Rationale**: Travel management company reports frequently list flight sectors (e.g. `LHR-JFK`) but omit miles/kilometers. This algorithm guarantees continuous carbon calculation with zero manual overhead.

---

## 3. Core Development Assumptions

1. **Static Carbon Intensity Factors**: We assume that carbon emission conversion calculations normalize raw inputs to standardized energy/travel units (`L`, `kWh`, `km`) as the primary scope metric. The actual carbon intensity conversion calculations are expected to take place in external downstream analytics components using these metrics.
2. ~~**Single Tenant Operations**: Although the schema is built for multi-tenancy, our MVP assumes a single analyst user owns auditing tasks for their organization during standard active sessions.~~ **Superseded** — Phase 2 implemented full multi-role RBAC (Org Admin / Analyst / Auditor / Viewer, plus a cross-tenant Platform Admin), and Phase 6's governance workflow deliberately assigns different roles to different actions (e.g. `submit` vs. `approve`/`reject` vs. soft-delete are three different permission classes — see [`AUTH_RBAC.md`](AUTH_RBAC.md) §3 and [`GOVERNANCE.md`](GOVERNANCE.md)'s Governance Architecture Overview). This MVP-era assumption no longer describes the system.
3. **Multipart Stream Boundaries**: We assume that typical files submitted to the ingestion portal fit easily on standard web server ephemeral disks. Files are written to temporary disk spaces and deleted in a `finally` block post-ingest.

---

## 4. Product Management (PM) Resolutions

- **Q: How should we treat a line item that has negative quantities?**
  - *Resolution*: Negative quantities are treated as unrecoverable errors (`FAILED`). We log the error at the row level but continue parsing subsequent lines to prevent a single typo from blocking an entire audit batch upload.
- **Q: What happens if an anomalous/suspicious record is approved by mistake?**
  - *Resolution (superseded by Phase 6c/6d — see [`GOVERNANCE.md`](GOVERNANCE.md) for the current design)*: the single-step "Confirm & Lock" flow described here no longer exists. A record must be explicitly `submit`ted before it can be `approve`d (Draft/Suspicious → Submitted → Approved, Phase 6c); once `APPROVED`, a strict model-level validator blocks all further *business-data* edits on that row. "Deletions" specifically are handled differently than this entry originally implied: an `APPROVED` row cannot be hard-deleted at all (Phase 6d — `EmissionRecord.delete()` raises unconditionally), but it **can** be reversibly soft-deleted (hidden, `is_deleted=True`) and later restored, without losing its certified state — soft-deleting an approved record does not "unseal" it or touch its business fields.
- **Q: How does the system handle duplicate files uploaded by analysts?**
  - *Resolution*: The system treats each file upload as a unique `UploadBatch` and maps them to an auto-generated UUID primary key. This prevents file overwrites and lets auditors track data lineage across historical uploads.
