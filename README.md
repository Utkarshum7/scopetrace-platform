# ScopeTrace — Enterprise Carbon Accounting & ESG Data Platform

> **ScopeTrace**  
> A production-grade, full-stack enterprise platform that ingests corporate greenhouse gas (GHG) emission data from heterogeneous sources, normalizes it to standardized base activity units — the foundation for CO₂ equivalent (CO₂e) reporting — and guides analysts through an immutable audit and review workflow across Scopes 1, 2, and 3.

---

## 📋 Project Overview

Modern enterprises collect environmental impact data across fragmented systems: fuel purchases in CSVs, electricity invoices in utility tables, and business travel bookings in JSON files. 

**ScopeTrace** acts as a centralized middleware that:
1. **Ingests** raw data from varied formats via a unified, strategy-pattern ingestion engine.
2. **Validates** data points in real time, detecting format errors, missing metrics, and statistical anomalies.
3. **Normalizes** heterogeneous units (e.g., liters of diesel, kWh of electricity, passenger-kilometers) into standardized base activity units (liters, kWh, km) — the basis for downstream CO₂e calculation.
4. **Guides Analysts** through a verification ledger to flag anomalies, review suspicious entries, and approve records.
5. **Secures Audit Trails** by writing approved records into an append-only, immutable ledger (enforced at the model layer) for tamper-evident reporting.

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
* **Interactive Frontend Dashboard**: Rich visual metrics showing total emissions, pending reviews, batch statuses, and a streamlined drag-and-drop file upload center.
* **Production-Ready Configurations**: Pre-configured WSGI environment with Gunicorn, WhiteNoise static assets, PostgreSQL compatibility, and Vercel routing rewrites.

---

## 🛠️ Tech Stack

| Layer | Technologies |
| :--- | :--- |
| **Frontend** | React 18, Vite 5, Tailwind CSS 3, Axios |
| **Backend** | Django 6.0, Django REST Framework 3.17, Python Decouple |
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
5. **Append-Only Lock**: The record is locked. An `AuditTrail` entry is written capturing the state change, the analyst, a timestamp, and the justification. (A cryptographic hash-chain over the ledger is planned for a later phase.)

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

### Backend Configuration (`backend/.env`)
Create a `.env` file in the `backend/` directory based on `backend/.env.example`:

```ini
# Security Configuration
SECRET_KEY=your-production-secret-key
DEBUG=False
ALLOWED_HOSTS=scopetrace-api.onrender.com,localhost,127.0.0.1

# Database Configuration (Auto-injected in Render, defaults to SQLite locally)
DATABASE_URL=postgres://user:password@hostname:5432/dbname

# CORS Allowed Origins
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://scopetrace.vercel.app

# Optional: Automatic Superuser Seeding
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_PASSWORD=yoursecurepassword
DJANGO_SUPERUSER_EMAIL=admin@example.com
```

### Frontend Configuration (`frontend/.env`)
Create a `.env` file in the `frontend/` directory based on `frontend/.env.example`:

```ini
# URL of the backend Django API
VITE_API_URL=https://scopetrace-api.onrender.com
```

---

## 🐳 Quick Start with Docker Compose (Recommended)

The entire stack — PostgreSQL, the Django API, and the built frontend — runs in containers with a single command. Postgres data persists in a named volume across restarts and rebuilds (no ephemeral data loss).

```bash
# From the repository root
docker compose up --build
```

| Service | URL |
| :--- | :--- |
| Frontend | http://localhost:8080 |
| API (browsable) | http://localhost:8000/api/ |
| Health check | http://localhost:8000/healthz |
| Django Admin | http://localhost:8000/admin/ (`admin` / `admin12345` by default) |

On first start the API container automatically runs migrations, collects static files, and seeds baseline data (one demo Organization, three DataSources, and the admin user) via the `bootstrap_data` command — so the app is usable immediately. Override defaults (DB name/credentials, admin password, `SECRET_KEY`) with a root `.env` file or shell environment; see `docker-compose.yml` for the variables.

To stop and remove the containers (data volume retained):

```bash
docker compose down          # keeps the pgdata volume
docker compose down -v       # also deletes the database volume
```

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
   python manage.py bootstrap_data
   ```
   This creates a demo **Organization**, the three **Data Sources** (`SAP_FUEL`, `UTILITY_ELECTRICITY`, `CORP_TRAVEL`), and an admin user. To create these manually instead, use Django Admin:
   * Create an **Organization** (e.g., *Acme Corporation*).
   * Create three **Data Sources** matching your organization:
     * Name: `SAP Fuel Feed` · Type: `SAP_FUEL`
     * Name: `Utility Feed` · Type: `UTILITY_ELECTRICITY`
     * Name: `Travel Feed` · Type: `CORP_TRAVEL`
3. **Upload Files**:
   * Open the frontend dashboard at `http://localhost:5173/` and navigate to the **Upload Center**.
   * Upload sample files from the `sample_data/` folder (e.g., `sap_fuel_sample.csv` matching `SAP Fuel Feed`).
4. **Ingestion & Validation**:
   * Verify that the batch upload completes and is parsed.
   * View the newly created records in the **Review Ledger**. Anomalous metrics (e.g., highly abnormal fuel quantities) will be marked as `SUSPICIOUS`.
5. **Analyst Audit**:
   * Click the **Approve** button on a record.
   * Provide your analyst justification reasoning.
   * Confirm the lock. The record status will update to `APPROVED` and write an immutable block into the database `AuditTrail`.

---

## 📊 API Endpoints Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/healthz` | Database-aware health probe (200 healthy / 503 if DB unreachable) |
| `GET` | `/api/organizations/` | List all tenant organizations |
| `GET` | `/api/datasources/` | List all configured data sources |
| `POST` | `/api/upload/sap/` | Upload and parse SAP Fuel CSV |
| `POST` | `/api/upload/utility/` | Upload and parse Utility Electricity CSV |
| `POST` | `/api/upload/travel/` | Upload and parse Corporate Travel JSON |
| `GET` | `/api/batches/` | List all ingestion batches |
| `GET` | `/api/records/` | List emission records (supports status & anomaly filters) |
| `POST` | `/api/records/{id}/approve/` | Approve a record and lock it in the Audit Trail |

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

## 🔮 Future Improvements

1. **Real-Time Stream Ingestion**: Integrate Kafka or RabbitMQ pipelines to ingest live utility readings directly from IoT smart meters instead of manual files.
2. **Blockchain Audit Ledger**: Export approved transaction hashes to a private blockchain network (e.g., Hyperledger Fabric) to achieve high-trust external regulatory validation.
3. **Machine Learning Anomaly Detection**: Implement isolation forests or autoencoders to find subtle multivariate anomalies in ESG reporting patterns rather than basic threshold heuristics.
4. **Standardized Framework Compliance**: Auto-map emission records to ESG standards (CSRD, GRI, SASB) and export ready-made PDF compliance templates.
5. **Automated Supplier Portals (Scope 3)**: Enable secure third-party portals with OAuth2 authentication where external partners upload shipping and manufacturing statistics directly.
