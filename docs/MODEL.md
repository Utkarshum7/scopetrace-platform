# Data Model & Database Architecture Guide (`MODEL.md`)

This document outlines the schema design, multi-tenancy partitioning, data lineage, validation states, and normalization logic implemented on the ScopeTrace platform database layer.

---

## 1. Schema Design and Entities Relationship

The database is built on Django's ORM and structured into three functional apps: `core` (organization assets), `ingestion` (ingestion pipeline + governance-versioned records), and `audit` (append-only, hash-chained ledger). Phase 6 added `EmissionRecordVersion` and `AuditChainState` — the full governance design (not just the schema) is documented in [`GOVERNANCE.md`](GOVERNANCE.md), which this section defers to rather than duplicates.

```mermaid
erDiagram
    Organization ||--o{ DataSource : owns
    Organization ||--o{ UploadBatch : owns
    Organization ||--o{ EmissionRecord : owns
    Organization ||--o{ AuditTrail : monitors
    Organization ||--o{ AuditChainState : "has one chain tip per"
    Organization ||--o{ EmissionRecordVersion : "denormalized owns"
    DataSource ||--o{ UploadBatch : sources
    UploadBatch ||--o{ EmissionRecord : contains
    EmissionRecord ||--o{ AuditTrail : logs
    EmissionRecord ||--o{ EmissionRecordVersion : "has immutable snapshots"
    EmissionRecord ||--o{ EmissionCalculation : "has (PROTECTed)"
```

`EmissionRecord.organization`/`.batch` and `EmissionCalculation.
emission_record` are `on_delete=PROTECT` (Phase 6d) — a batch or
organization with any records, and a record with any calculations, can no
longer be hard-deleted at all. `AuditTrail.organization`/
`EmissionRecordVersion.organization` are likewise `PROTECT` (Phase 6a/6b).
`EmissionRecord` itself blocks hard deletion entirely (`.delete()` raises
unconditionally) — see [`GOVERNANCE.md`](GOVERNANCE.md) §6d for the
reversible soft-delete mechanism that replaces it.

### Core Entities

#### 1. `Organization` (Tenant Separation)
Exposes tenant boundaries. Every master configuration and transactional record belongs strictly to a specific organization.
- **Key Fields**:
  - `id`: UUID (Primary Key)
  - `name`: String (Name of the corporate entity)
  - `created_at` / `updated_at`: Timestamps

#### 2. `DataSource` (Extraction Adapters Configuration)
Exposes the specific configuration, parser types, and ingestion adapters for data feeds.
- **Key Fields**:
  - `id`: UUID (Primary Key)
  - `organization`: ForeignKey to `Organization` (Multi-tenant partition)
  - `name`: String (e.g. "SAP Q1 Carbon Feed", "London HQ Billing")
  - `source_type`: Choice Enum (`SAP_FUEL`, `UTILITY_ELECTRICITY`, `CORP_TRAVEL`)

#### 3. `UploadBatch` (Ingestion Context & Statistics)
Tracks files processed through the parser adapters and aggregates run statistics.
- **Key Fields**:
  - `id`: UUID (Primary Key)
  - `organization`: ForeignKey to `Organization`
  - `data_source`: ForeignKey to `DataSource`
  - `file_name`: String (The exact report name)
  - `status`: Choice Enum (`PROCESSING`, `COMPLETED`, `FAILED`)
  - `total_rows` / `failed_rows`: Numerical metrics tracking pipeline errors

#### 4. `EmissionRecord` (Normalized Carbon Account Ledger)
The transactional ledger storing clean, normalized greenhouse gas emissions for analytics and review.
- **Key Fields**:
  - `id`: UUID (Primary Key)
  - `organization`: ForeignKey to `Organization` (`PROTECT` — Phase 6d)
  - `batch`: ForeignKey to `UploadBatch` (`PROTECT` — Phase 6d)
  - `row_index`: Integer (Maintains file index for line-item error auditing)
  - `raw_data_payload`: JSONField (Exposes the original file parameters for review)
  - `status`: Choice Enum (`DRAFT`, `SUSPICIOUS`, `VALIDATED`, `SUBMITTED`, `APPROVED`, `REJECTED`, `FAILED`) — the fixed approval workflow, Phase 6c; see [`GOVERNANCE.md`](GOVERNANCE.md) §6c for the full transition graph
  - `is_suspicious`: Boolean flag indicating outlier warnings
  - `validation_errors`: JSONField mapping validation error arrays
  - `normalized_value`: High-precision Decimal holding the normalized value in the base activity unit (L / kWh / km) — the basis for downstream CO₂e calculation
  - `normalized_unit`: String base unit (`L`, `kWh`, `km`)
  - `scope_category`: Choice Enum (`SCOPE_1` for fuel, `SCOPE_2` for power, `SCOPE_3` for travel)
  - `approved_by` / `approved_at`: ForeignKey to User / DateTime (Attribution tracking)
  - `is_deleted` / `deleted_at`: Boolean (indexed) / DateTime — reversible soft-delete state, Phase 6d, orthogonal to `status` (deletion never transitions through the workflow). `EmissionRecord.delete()` itself raises unconditionally; see [`GOVERNANCE.md`](GOVERNANCE.md) §6d.

#### 5. `AuditTrail` (Append-Only, Hash-Chained Ledger)
Immutable, tamper-*evident* historical record tracking every governance action (submission, approval, rejection, recalculation, soft-delete, restore).
- **Key Fields**:
  - `id`: UUID (Primary Key)
  - `organization`: ForeignKey to `Organization` (`PROTECT` — Phase 6a)
  - `record`: ForeignKey to `EmissionRecord` (`SET_NULL`)
  - `record_uuid_backup`: UUID (Backup parameter keeping the audit trace intact if a record is dropped)
  - `action`: String (e.g. `RECORD_APPROVAL`, `RECORD_SOFT_DELETE` — six action names exist today, enumerated in [`GOVERNANCE.md`](GOVERNANCE.md)'s Governance Architecture Overview)
  - `changed_by`: ForeignKey to User model
  - `changes`: JSONField mapping state diffs (e.g. `{"status": ["DRAFT", "APPROVED"], "record_version": 3}`)
  - `reason`: Text (Analyst rationale)
  - `sequence` / `prev_hash` / `entry_hash`: Phase 6a — a per-organization monotonic SHA-256 hash chain. `entry_hash` is computed over a canonical serialization including `prev_hash`, so altering any historical row breaks every subsequent link. Verify via `GET /api/audit/verify/` or `manage.py verify_audit_chain`. `AuditTrail.delete()` raises unconditionally; bulk `.delete()`/`.update()` are blocked at the QuerySet level too.

#### 6. `EmissionRecordVersion` (Immutable Business-State Snapshot — Phase 6b)
A full, typed snapshot of an `EmissionRecord`'s business state at one point in time — reconstructable, not just an event log. Created automatically whenever a business field (including `status`, `is_deleted`) changes.
- **Key Fields**: mirrors `EmissionRecord`'s own business fields (`status`, `is_suspicious`, `scope_category`, `normalized_value`, `normalized_unit`, `approved_by`/`approved_at`, `validation_errors`, `raw_data_payload`, `is_deleted`/`deleted_at`) plus `version_number` (monotonic per record), `calculation` (the `EmissionCalculation` current at snapshot time), `created_by`/`created_at`/`reason` (provenance). `record` is `SET_NULL` (not `PROTECT`) — a version's own tenant scoping comes from its denormalized `organization` FK (`PROTECT`).
- Never edited or deleted after creation (`clean()`/`delete()` block it; bulk operations blocked too). See [`GOVERNANCE.md`](GOVERNANCE.md) §6b.

#### 7. `AuditChainState` (Per-Organization Chain Tip — Phase 6a)
One row per organization: the current hash-chain tip (`last_sequence`, `last_hash`). Mutable bookkeeping, not itself a governance record — `AuditTrail` is the ledger, this is just the "what's next" counter, locked via `select_for_update()` on every append for atomic sequence assignment under concurrent writers.

---

## 2. Robust Multi-Tenancy Architecture

We enforce **logical multi-tenancy** partitioning:
1. Every master-data configuration (`DataSource`) and transactional element (`UploadBatch`, `EmissionRecord`, `AuditTrail`) contains a direct ForeignKey referencing `Organization`.
2. View queries and bulk ingestion operations strictly partition database operations using the tenant ID.
3. This logical layout provides high performance, database index efficiency (filtering by `organization_id`), and is perfectly suited for SaaS deployments.

---

## 3. Data Lineage and Row-Level Tracing

Data integrity is fully auditable from raw data file to locked carbon accounts:
- **`raw_data_payload` Preservation**: The parser adapters stream raw records to the database as a JSONField. The analyst can view the exact spreadsheet rows (e.g. original billing periods, cost currencies) in their sidebar review cards.
- **`row_index` Matching**: Line numbers from raw logs are written to the database. If an ingestion batch fails due to corrupt schemas, the analyst sees the exact line index (e.g., "Row #4: negative fuel amount") to quickly edit source files.

---

## 4. Ingestion Validation States (Two-Tier Model)

The validation adapter uses a strict, non-destructive **two-tier** system:

### Tier 1: `FAILED` (Critical Structural Issues)
- **Trigger**: Non-numeric numbers, negative amounts, future dates, corrupt structures, or unknown units.
- **Result**: Ingestion continues, but the specific row receives `status = FAILED`, `normalized_value = null`, and validation errors are saved to `validation_errors`. The batch status remains intact but tracks the failed row count. Analysts cannot approve a `FAILED` record.

### Tier 2: `SUSPICIOUS` (Data Anomaly Warnings)
- **Trigger**: Dates posted > 365 days ago, or quantities exceeding a dynamic **outlier detection limit** (calculated as $> 3 \times \text{median}$ of the ingestion batch).
- **Result**: The row is parsed and normalized successfully (`status = SUSPICIOUS`, `is_suspicious = True`), and a warning is logged to `validation_errors`. The analyst must review and justify the anomaly in the dashboard before lock-securing it.

---

## 5. High-Precision Normalization Logic

All unit conversions are normalized to base activity scales using Python’s high-precision `Decimal` object (CO₂e emission factors are applied in a later phase):

| Feed Type | Source Unit | Normalization Target | Conversion Ratio / Logic | Scope Target |
| :--- | :---: | :---: | :--- | :---: |
| **SAP Fuel** | `L` (Liters) | `L` (Liters) | $1.0\times$ Liters | **Scope 1** |
| | `M3` (Cubic Meters) | `L` (Liters) | $1000.0\times$ Liters | **Scope 1** |
| **Utility Power** | `kWh` | `kWh` | $1.0\times$ kWh | **Scope 2** |
| | `MWh` | `kWh` | $1000.0\times$ kWh | **Scope 2** |
| **Corp Travel** | `km` (Rail/Flight) | `km` | $1.0\times$ km (Haversine if missing) | **Scope 3** |

### Advanced Business Air-Seating Multipliers
Corporate travel uses **DEFRA seating class multipliers** to compute accurate passenger carbon footprints:
- **`ECONOMY`**: $1.0\times$ distance
- **`PREMIUM_ECONOMY`**: $1.6\times$ distance
- **`BUSINESS`**: $2.9\times$ distance
- **`FIRST`**: $4.0\times$ distance

---

## 6. Carbon Intelligence Engine (Phase 3)

The `apps.carbon` app adds the emission-factor and calculation layers. Reference
tables are **global** (shared, no tenant FK); calculations are **tenant-scoped**.
Full design in [`CARBON_ENGINE_DESIGN.md`](CARBON_ENGINE_DESIGN.md).

| Model | Scope | Purpose |
| :--- | :--- | :--- |
| `Region` | global | Resolution geography (ISO / GLOBAL) |
| `ActivityType` | global | Controlled vocabulary decoupling parsers from factors |
| `EmissionFactorDataset` | global | Versioned, provenance-tracked factor container (immutable once ACTIVE) |
| `EmissionFactor` | global | Per-unit CO₂e value (effective-dated, region-aware, per-gas seam) |
| `UnitConversion` | global | Dimension-checked Decimal unit conversions |
| `GwpSet` | global | GWP set — reserved seam for future per-gas CO₂e |
| `ActivityMapping` | global | Maps `data_source_type` + match key → `ActivityType` |
| `OrgFactorPolicy` | tenant | Per-org preferred publisher / region / strict mode |
| `EmissionCalculation` | tenant | Immutable, factor-pinned, explainable CO₂e result (one `is_current` per record) |

**`EmissionRecord`** is unchanged and holds **only activity data**. CO₂e lives
**exclusively** in `EmissionCalculation` (never denormalized onto the record),
which keeps the approval audit-lock intact for locked records.

**Analytics fact table (Phase 4):** `EmissionCalculation` also carries denormalized
`scope`, `reporting_date`, and `reporting_month` with composite indexes, so the
Metrics API runs indexed `SUM/GROUP BY` (by scope, over time) without joins. See
[`METRICS_ANALYTICS.md`](METRICS_ANALYTICS.md).
