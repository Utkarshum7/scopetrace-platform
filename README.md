# ScopeTrace — Enterprise Carbon Accounting & ESG Data Platform

[![Backend CI](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/backend-ci.yml/badge.svg)](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/backend-ci.yml)
[![Frontend CI](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/frontend-ci.yml/badge.svg)](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/frontend-ci.yml)
[![Docker Build Verification](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/docker-build.yml/badge.svg)](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/docker-build.yml)
[![Secret Scan](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/Utkarshum7/breathe-esg-platform/actions/workflows/secret-scan.yml)

> **ScopeTrace**  
> A production-grade, full-stack enterprise platform that ingests corporate greenhouse gas (GHG) emission data from heterogeneous sources, normalizes it to standardized base activity units — the foundation for CO₂ equivalent (CO₂e) reporting — and guides analysts through an immutable audit and review workflow across Scopes 1, 2, and 3.

---

## 📋 Project Overview

Modern enterprises collect environmental impact data across fragmented systems: fuel purchases in CSVs, electricity invoices in utility tables, and business travel bookings in JSON files. 

**ScopeTrace** acts as a centralized middleware that:
1. **Ingests** raw data from varied formats via a unified, strategy-pattern ingestion engine.
2. **Validates** data points in real time, detecting format errors, missing metrics, and statistical anomalies.
3. **Normalizes & computes CO₂e**: converts heterogeneous units (liters of diesel, kWh of electricity, passenger-kilometers) into standardized base activity units, then computes CO₂e via the **Carbon Intelligence Engine** — versioned, provenance-tracked emission factors (DEFRA/EPA/IPCC-ready) with fully explainable, factor-pinned calculations.
4. **Guides Analysts** through a verification ledger to flag anomalies, review suspicious entries, and approve records.
5. **Secures Audit Trails** by writing approved records into an append-only, immutable ledger (enforced at the model layer) for tamper-evident reporting.
6. **Enforces Enterprise Access Control** via JWT authentication, role-based permissions, and per-organization tenant isolation — see [`docs/AUTH_RBAC.md`](docs/AUTH_RBAC.md).

---

## ✨ Features

* **Multi-Adapter Ingestion**: Supports extensible strategy-pattern parsers for heterogeneous inputs:
  * **SAP Fuel Feed** (CSV parser for fleet fuel purchases)
  * **Utility Electricity Feed** (CSV parser for facility electricity metrics)
  * **Corporate Travel Feed** (JSON parser for flight/train travel bookings)
* **High-Fidelity Validation Pipeline**: Real-time validation checking structure, types, mandatory fields, and range boundaries.
* **Suspicious Anomaly Detection**: Automatically flags records exceeding historical bounds or standard operating ranges (e.g., negative fuel values or abnormally high kWh readings).
* **Analyst Review Ledger**: A clean dashboard UI for analysts to filter records, review anomalies, and enter justifications for approval.
* **Immutable Audit Logging**: Employs an append-only ledger model that locks record states post-approval and tracks the analyst's ID, timestamp, and reasoning.
* **Django Admin Integration**: Allows administrators to register organizations (tenants) and configure data sources on the fly.
* **Enterprise Identity & Access**: JWT authentication (access/refresh tokens, rotation, logout blacklist), four organization-scoped roles (Org Admin, ESG Analyst, Auditor, Viewer) plus a cross-tenant Platform Admin, and server-side multi-tenant isolation enforced at the API layer.
* **Carbon Intelligence Engine**: versioned, provenance-tracked emission-factor datasets (DEFRA/EPA/IPCC/country-ready) with effective-dated, region-aware factor resolution; Decimal-precise, factor-pinned, immutable CO₂e calculations; a self-contained explainability trace on every result; a staged pipeline with reserved hooks for future AI modules; and idempotent import/seed/backfill commands. See [`docs/CARBON_ENGINE_DESIGN.md`](docs/CARBON_ENGINE_DESIGN.md).
* **Analytics & Dashboards**: a cached, tenant-scoped Metrics API (summary/time-series/breakdown + audit-activity and cross-tenant views) powering a **role-aware, pluggable dashboard** — professional KPI cards with trend deltas, responsive charts (behind a swappable chart abstraction), loading skeletons, and empty/error states. Pagination, standardized filtering, streaming CSV export, and API rate-limiting. See [`docs/METRICS_ANALYTICS.md`](docs/METRICS_ANALYTICS.md).
* **Interactive Frontend Dashboard**: Rich visual metrics showing total emissions, pending reviews, batch statuses, and a streamlined drag-and-drop file upload center.
* **Production-Ready Configurations**: Pre-configured WSGI environment with Gunicorn, WhiteNoise static assets, PostgreSQL compatibility, and Vercel routing rewrites.

---

## 🛠️ Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | React 18, Vite 5, Tailwind CSS 3, Axios, TanStack Query, Recharts |
| **Backend** | Django 6.0, Django REST Framework 3.17, SimpleJWT, django-filter, Python Decouple |
| **Cache / Rate limiting** | Redis (optional; local-memory fallback) |
| **Database** | PostgreSQL 16 (required in production) · SQLite (local dev only) |
| **Containerization** | Docker · Docker Compose (PostgreSQL + API + frontend) |
| **Asset Serving** | WhiteNoise 6.9 |
| **Production Server** | Gunicorn 23.0 |
| **Deployment Target** | **Frontend**: Vercel · **Backend & Database**: Render (portable via Docker) |

---

## 📐 Architecture & Workflow

```
[ Django Admin Configures Organization & DataSource ]
                      ↓
[ Analyst Uploads Raw File (CSV/JSON) in Frontend Upload Center ]
                      ↓
  [ API Routes Request to strategy-pattern Ingestion Engine ]
                      ↓
 [ Adapter Strategy Parses File ] → [ Validation & Anomaly Detection Pipeline ]
                                                     ↓
                                [ EmissionRecord created (DRAFT/SUSPICIOUS/FAILED) ]
                                                     ↓
                                  [ Analyst Dashboard Ledger Review ]
                                                     ↓
                   [ Analyst Approves Record (with custom reason) ]
                                                     ↓
             [ Atomic Transaction Locks Record & Generates AuditTrail Log ]
```

1. **Administration**: Admin sets up an `Organization` and maps a `DataSource` to it (e.g., a Travel Feed adapter).
2. **Ingestion Strategy**: The file is processed using the appropriate strategy (`sap`, `utility`, or `travel`).
3. **Validation & Normalization**: Data is checked for schema conformity and converted to standardized base activity units (the basis for CO₂e).
4. **Ledger Auditing**: Suspicious rows (e.g., abnormal values) are flagged for human review. Once verified, the analyst approves the record.
5. **Append-Only Lock**: The record is locked. An `AuditTrail` entry is written capturing the state change, the analyst, a timestamp, and the justification, chained into a per-organization SHA-256 hash-chain (tamper-*evident* — see [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) §6a) verifiable via `GET /api/audit/verify/`.

---

## 🌐 Deployment Links

| Environment / Service | Live URL |
| :--- | :--- |
| **Frontend (Vercel)** | [https://scopetrace.vercel.app](https://scopetrace.vercel.app) |
| **Backend API (Render)** | [https://scopetrace-api.onrender.com/api/](https://scopetrace-api.onrender.com/api/) |
| **Django Admin Portal** | [https://scopetrace-api.onrender.com/admin/](https://scopetrace-api.onrender.com/admin/) |

> ℹ️ The hosted environments are being re-provisioned under the ScopeTrace namespace. Update these links once the new Render/Vercel services are live.

---

## ⚙️ Environment Variables

Copy `backend/.env.example` → `backend/.env` and `frontend/.env.example` →
`frontend/.env`, then fill in real values. Configuration **fails closed**:
with `DEBUG=False` the backend refuses to boot unless `SECRET_KEY` and
`DATABASE_URL` are set, rejects a wildcard `ALLOWED_HOSTS`, and requires
`STORAGE_BACKEND=s3` (with S3 credentials).

The full reference — every variable, its default, and which subsystem reads
it (Celery, storage, email, throttling, JWT, Docker-Compose-only infra
credentials) — is in [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) §4, not duplicated here.

---

## 🐳 Quick Start with Docker Compose (Recommended)

The full stack — PostgreSQL, Redis, MinIO (S3-compatible storage), the
Django API, a Celery worker, Celery Beat, and the built frontend — runs in
containers with a single command. Postgres/MinIO/Beat data all persist in
named volumes across restarts and rebuilds.

```bash
# From the repository root
docker compose up --build
```

| Service | URL |
| :--- | :--- |
| Frontend | http://localhost:8080 |
| API (browsable) | http://localhost:8000/api/ |
| Health check | http://localhost:8000/healthz |
| Worker health check | http://localhost:8000/healthz/worker/ |
| Django Admin | http://localhost:8000/admin/ (`admin` / `admin12345` by default) |
| MinIO console | http://localhost:9001 (`scopetrace` / `scopetrace123` by default) |

On first start the API container automatically runs migrations, collects static files, and seeds baseline data (one demo Organization, three DataSources, the admin user, and one demo user per role) via the `bootstrap_data`/`seed_carbon` commands — so the app, including full async upload processing, is usable immediately. Override defaults (DB/MinIO credentials, admin password, `SECRET_KEY`) with a root `.env` file or shell environment; see `docker-compose.yml` for the variables, or the full reference in [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md).

```bash
docker compose up --scale worker=3 -d          # horizontal worker scaling, zero code change
docker compose --profile monitoring up -d flower  # optional Celery monitoring UI, see docs/FLOWER.md
docker compose down          # keeps all named volumes
docker compose down -v       # also deletes them (fresh start)
```

Full operational detail — health checks, Celery/queue operations, backup & recovery, troubleshooting, production deployment — lives in [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) and [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md).

---

## 🚀 Local Setup Instructions (without Docker)

> **Configuration fails closed.** With `DEBUG=False` the backend refuses to start unless `SECRET_KEY` and `DATABASE_URL` are set, and it rejects a wildcard `ALLOWED_HOSTS`. For local development set `DEBUG=True` (SQLite is then used automatically when `DATABASE_URL` is blank). See `backend/.env.example`.

### Prerequisites
* Python 3.12+ (WSL recommended on Windows)
* Node.js 18+ & npm
* Git

### 1. Backend Setup
Navigate to the backend directory, create a virtual environment, install dependencies, and run migrations:

```bash
# Move to backend directory
cd backend

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate          # On Linux/macOS/WSL
# OR: .\venv\Scripts\activate     # On Windows CMD/Powershell

# Install requirements
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Create administrative user
python manage.py createsuperuser

# Start Django development server
python manage.py runserver
```
The Django API will be accessible at [http://localhost:8000/api/](http://localhost:8000/api/).

### 2. Frontend Setup
Open a new terminal window, navigate to the frontend directory, install npm packages, and run the development server:

```bash
# Move to frontend directory
cd frontend

# Install packages
npm install

# Start Vite dev server
npm run dev
```
The application dashboard will be accessible at [http://localhost:5173/](http://localhost:5173/).

### 3. Running Tests
To run the automated Django test suite covering parsers, validators, API views, and audit trail logic:

```bash
cd backend
python manage.py test --verbosity=2
```

---

## 🔄 Sample Usage Workflow

To test the ingestion and analyst flow locally or in production:

1. **Access Django Admin**: Go to `http://localhost:8000/admin/` and log in.
2. **Seed Core Metadata** — run the bootstrap command (idempotent; also runs automatically under Docker and on Render release):
   ```bash
   python manage.py bootstrap_data --demo-users
   ```
   This creates a demo **Organization**, the three **Data Sources** (`SAP_FUEL`, `UTILITY_ELECTRICITY`, `CORP_TRAVEL`), an admin user, and (with `--demo-users`, also enabled by default under Docker Compose) one demo user per role — `orgadmin` / `analyst` / `auditor` / `viewer`, password `demo12345`. To create these manually instead, use Django Admin:
   * Create an **Organization** (e.g., *Acme Corporation*).
   * Create three **Data Sources** matching your organization:
     * Name: `SAP Fuel Feed` · Type: `SAP_FUEL`
     * Name: `Utility Feed` · Type: `UTILITY_ELECTRICITY`
     * Name: `Travel Feed` · Type: `CORP_TRAVEL`
   * Create a **Membership** binding a user to the organization with a role (Org Admin / ESG Analyst / Auditor / Viewer).
3. **Sign In**: Open `http://localhost:5173/` and log in as `analyst` (or another demo user). The JWT session is required to reach the Dashboard, Upload Center, or Review Ledger.
4. **Upload Files**:
   * Navigate to the **Upload Center** (visible to Org Admin / ESG Analyst roles).
   * Upload sample files from the `sample_data/` folder (e.g., `sap_fuel_sample.csv` matching `SAP Fuel Feed`).
5. **Ingestion & Validation**:
   * Verify that the batch upload completes and is parsed.
   * View the newly created records in the **Review Ledger**. Anomalous metrics (e.g., highly abnormal fuel quantities) will be marked as `SUSPICIOUS`.
6. **Analyst Audit**:
   * Click the **Approve** button on a record (Org Admin / ESG Analyst / Auditor roles).
   * Provide your analyst justification reasoning.
   * Confirm the lock. The record status will update to `APPROVED` and write an immutable block into the database `AuditTrail`.

---

## 📊 API Endpoints Reference

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/healthz` | none | Database-aware health probe (200 healthy / 503 if DB unreachable) |
| `GET` | `/healthz/worker/` | none | Celery worker liveness (real broker round trip + Beat heartbeat freshness) |
| `POST` | `/api/auth/login/` | none | Obtain JWT access + refresh tokens and the user profile |
| `POST` | `/api/auth/refresh/` | none | Exchange a refresh token for a new access/refresh pair (rotated) |
| `POST` | `/api/auth/logout/` | Bearer | Blacklist the refresh token (logout) |
| `GET` | `/api/me/` | Bearer | Current user, active memberships, and resolved active organization |
| `GET` | `/api/organizations/` | Bearer | List organizations the caller belongs to (all orgs for Platform Admin) |
| `GET` | `/api/datasources/` | Bearer | List data sources for the active organization |
| `POST` | `/api/upload/sap/` | Bearer (Org Admin / Analyst) | Upload and parse SAP Fuel CSV |
| `POST` | `/api/upload/utility/` | Bearer (Org Admin / Analyst) | Upload and parse Utility Electricity CSV |
| `POST` | `/api/upload/travel/` | Bearer (Org Admin / Analyst) | Upload and parse Corporate Travel JSON |
| `GET` | `/api/batches/` | Bearer | List ingestion batches for the active organization |
| `GET` | `/api/records/` | Bearer | List emission records (status/anomaly filters; scoped to the active org) |
| `POST` | `/api/records/{id}/submit/` | Bearer (Org Admin / Analyst) | Submit a record for approval (Draft/Suspicious → Submitted) |
| `POST` | `/api/records/{id}/approve/` | Bearer (Org Admin / Analyst / Auditor) | Approve a Submitted record and lock it in the Audit Trail |
| `POST` | `/api/records/{id}/reject/` | Bearer (Org Admin / Analyst / Auditor) | Reject a Submitted record (reason required) |
| `GET` | `/api/records/{id}/workflow/` | Bearer | Current workflow status + legally available next actions |
| `POST` | `/api/records/{id}/recalculate/` | Bearer (Org Admin) | Recompute CO₂e with active factors (APPROVED records are frozen) |
| `GET` | `/api/records/{id}/versions/` | Bearer | List a record's immutable version history, newest first |
| `GET` | `/api/records/{id}/versions/{n}/` | Bearer | Retrieve one historical version snapshot |
| `GET` | `/api/records/{id}/versions/{n}/compare/` | Bearer | Field-by-field diff between a historical version and the current record |
| `DELETE` | `/api/records/{id}/` | Bearer (Org Admin) | Soft delete (reason required); the record is never physically removed |
| `POST` | `/api/records/{id}/restore/` | Bearer (Org Admin) | Restore a soft-deleted record to its prior status |
| `GET` | `/api/records/?deleted=true` | Bearer (Org Admin) | List soft-deleted records ("trash") |
| `GET` | `/api/activity-types/` | Bearer | Activity-type vocabulary (global reference) |
| `GET` | `/api/factor-datasets/` | Bearer | Emission-factor datasets with provenance (filter publisher/status) |
| `GET` | `/api/emission-factors/` | Bearer | Emission factors (filter activity_type/region) |
| `GET` | `/api/calculations/` | Bearer | CO₂e calculations for the active organization (scoped) |
| `GET` | `/api/records/export/` | Bearer | Streaming CSV export of records (filters + CO₂e columns) |
| `GET` | `/api/metrics/summary/` | Bearer | KPI summary (total tCO₂e, by scope, coverage, trend basis) |
| `GET` | `/api/metrics/timeseries/` | Bearer | Emissions over time (month/quarter/year, optional group_by=scope) |
| `GET` | `/api/metrics/breakdown/` | Bearer | tCO₂e by scope / activity_type / data_source |
| `GET` | `/api/metrics/activity/` | Bearer (Org Admin / Auditor) | Tenant audit-trail activity feed |
| `GET` | `/api/metrics/platform/` | Bearer (Platform Admin) | Cross-tenant overview + active organizations |
| `GET` | `/api/audit/verify/` | Bearer (Org Admin / Auditor) | Verify the organization's audit hash-chain integrity |
| `GET` | `/api/reports/compliance/` | Bearer (Org Admin / Auditor) | JSON compliance report (APPROVED-only, requires date_from/date_to) |
| `GET` | `/api/reports/compliance/csv/` | Bearer (Org Admin / Auditor) | Streaming CSV of the same compliance report |

Emission records include read-only `co2e_kg`, `co2e_tonnes`, `calculation_status`, `factor_provenance`, and an explainable `calculation_trace`.

**Pagination:** unbounded list endpoints (`records`, `calculations`, `batches`, `factor-datasets`, `emission-factors`) return `{count, next, previous, results}`; bounded selector lists (`organizations`, `datasources`, `activity-types`) return bare arrays. API requests are rate-limited.

See [`docs/AUTH_RBAC.md`](docs/AUTH_RBAC.md) for authentication/RBAC/tenancy, [`docs/CARBON_ENGINE_DESIGN.md`](docs/CARBON_ENGINE_DESIGN.md) for the Carbon Intelligence Engine, and [`docs/METRICS_ANALYTICS.md`](docs/METRICS_ANALYTICS.md) for the Metrics API, caching, pagination/export, and the role-aware dashboard.

### Carbon engine management commands

```bash
# Seed reference data + import & activate the bundled DEFRA 2024 factor subset
python manage.py seed_carbon

# Import a factor dataset (idempotent, provenance-tracked; --activate to publish)
python manage.py import_emission_factors --file factors.csv --publisher DEFRA \
  --dataset-version 2025 --region GB --valid-from 2025-01-01 --valid-to 2025-12-31 --activate

# Compute CO₂e for existing records (idempotent; --force recalculates, APPROVED frozen)
python manage.py backfill_calculations
```

---

## 🖼️ Screenshots Section

Below are placeholders representing key pages of the ScopeTrace platform. Replace these with product screenshots after deployment:

### ESG Command Dashboard
![ESG Command Dashboard](https://images.unsplash.com/photo-1460925895917-afdab827c52f?auto=format&fit=crop&w=1200&q=80)
*Overview of carbon footprints, total metric tonnes, batch success rates, and outstanding reviews.*

### Ingestion & Upload Center
![Upload Center](https://images.unsplash.com/photo-1551288049-bebda4e38f71?auto=format&fit=crop&w=1200&q=80)
*Drag-and-drop file interface for batch submissions mapped to specific organization adapters.*

### Ledger & Auditing View
![Review Ledger](https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=1200&q=80)
*Interactive table detailing raw versus normalized activity values, flags for suspicious values, and validation status.*

---

## 📚 Documentation

| Doc | Covers |
| :--- | :--- |
| [`ARCHITECTURE_OVERVIEW.md`](docs/ARCHITECTURE_OVERVIEW.md) | System component diagram, async pipeline sequence diagram, queue topology, links to every subsystem doc |
| [`RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) · [`RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) · [`SMOKE_TEST_CHECKLIST.md`](docs/SMOKE_TEST_CHECKLIST.md) · [`VERSION.md`](VERSION.md) | System-wide release-candidate audit with a classified risk register, what shipped per phase, manual post-deploy smoke test, current version |
| [`DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) | Local dev, production deployment, full environment variable reference, Docker/Compose usage, per-commit release checklist |
| [`OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) | Celery operations, queue/DLQ operational guide, Flower, health checks, common tasks, scaling, step-by-step runbooks |
| [`INCIDENT_RESPONSE.md`](docs/INCIDENT_RESPONSE.md) | Backup/restore (verified commands), disaster recovery, incident response, troubleshooting table |
| [`SECURITY.md`](docs/SECURITY.md) | Auth/RBAC/tenancy posture, secrets, rate limiting, dependency vulnerability status, known gaps |
| [`ROADMAP.md`](docs/ROADMAP.md) | Known limitations and the real Phase 6–10 plan |
| [`AUTH_RBAC.md`](docs/AUTH_RBAC.md) · [`CARBON_ENGINE_DESIGN.md`](docs/CARBON_ENGINE_DESIGN.md) · [`METRICS_ANALYTICS.md`](docs/METRICS_ANALYTICS.md) · [`MODEL.md`](docs/MODEL.md) · [`SOURCES.md`](docs/SOURCES.md) | Domain design docs — auth, carbon engine, analytics, data model, source formats |
| [`JOB_LIFECYCLE.md`](docs/JOB_LIFECYCLE.md) · [`RETRY_DLQ.md`](docs/RETRY_DLQ.md) · [`SCHEDULED_TASKS.md`](docs/SCHEDULED_TASKS.md) · [`NOTIFICATIONS.md`](docs/NOTIFICATIONS.md) | Async pipeline design — job lifecycle, retry/backoff/DLQ, Celery Beat, email notifications |
| [`FLOWER.md`](docs/FLOWER.md) · [`DOCKER.md`](docs/DOCKER.md) · [`CI_CD.md`](docs/CI_CD.md) | Monitoring, Docker image design, GitHub Actions CI |
| [`DECISIONS.md`](docs/DECISIONS.md) · [`TRADEOFFS.md`](docs/TRADEOFFS.md) | Architectural decisions made and scope deliberately deferred |

## 🗺️ Roadmap

Phases 0–9 (rebrand & infra → correctness → auth/RBAC → carbon engine →
metrics/analytics → production engineering → enterprise governance → AI →
UX/accessibility → production engineering & release readiness) are
complete — current version `0.9.0-rc1`, see [`VERSION.md`](VERSION.md)
and [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md). Known limitations
and the Phase 10+ launch plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).
