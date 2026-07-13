# Demo Deployment Plan (D7)

Status: **PREPARATION ONLY — no cloud resources have been created.** This
document, [`deployment/northflank/`](../deployment/northflank/), and the new
end-to-end test in
[`apps/ingestion/tests_demo_mode.py`](../backend/apps/ingestion/tests_demo_mode.py)
are the complete, reviewable deployment plan. Nothing here touches
`render.yaml`, `docker-compose.yml`, or production. Do not create any
account resources from this document without reviewing it first.

---

## 1. Platform evaluation

### Requirement recap

Demo Mode (`DEMO_MODE=True`, see [`ARCHITECTURE_OVERVIEW.md`](ARCHITECTURE_OVERVIEW.md)
§6 and [`DEMO_MODE_LATENCY.md`](DEMO_MODE_LATENCY.md)) needs exactly **one**
long-running web service — no Celery Worker, no Beat, and (confirmed by
reading `config/settings.py`) **no Redis**: `CACHES` only configures Redis
`if REDIS_URL:`, otherwise Django's local-memory cache is used automatically,
and `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` are never touched in eager
mode. What genuinely IS required, because `config/settings.py` fails closed
whenever `DEBUG=False` (which Demo Mode always runs as — it is not `DEBUG`):

- A real **PostgreSQL** database (`DATABASE_URL`) — SQLite is rejected outright.
- Real **S3-compatible object storage** (`STORAGE_BACKEND=s3` is mandatory)
  — local filesystem storage is a `DEBUG`-only convenience.

So "best free hosting platform" is really two decisions: a **compute**
platform for the single web service, and, since no compute platform in this
comparison bundles a genuinely free S3-compatible bucket, a **storage**
provider. Database can come from the compute platform's own offering or a
separate free provider.

### Compute platforms compared (current, verified this session)

| Platform | Free web-service tier | Free Postgres | Verdict |
| :--- | :--- | :--- | :--- |
| **Railway** | Trial gives $5 one-time credit, then $1/month free plan. One minimal always-on service (0.5GB/0.5vCPU) costs ~$0.80–1.00/mo on its own. | None that fits the credit — a small Postgres adds another ~$1–3/mo, exhausting the free credit within days. | **Ruled out.** Not a genuine free tier for an app + database; free-tier docs and multiple 2026 pricing breakdowns confirm this ([Railway pricing 2026](https://docs.railway.com/pricing/plans)). |
| **Koyeb** | 1 free instance (512MB/0.1vCPU), Frankfurt/Washington DC only, **scales to zero after 1 hour idle**. | Free Postgres capped at **5 hours of active time per month** and 1GB storage. | **Ruled out.** 5 hrs/month cannot serve a persistent demo database at all. Additionally: following Koyeb's **acquisition by Mistral AI (Feb 2026)**, new users can no longer sign up for the free/Starter tier — Pro ($29/mo+) is now the entry point ([Koyeb pricing FAQ](https://www.koyeb.com/docs/faqs/pricing)). Not available to a new deployment as of this writing. |
| **Fly.io** | **No permanent free tier since 2024.** New accounts get a 7-day/2-VM-hour trial with $5 credit, then billing. | Same trial-only status; a minimal Postgres runs ~$2.09/mo after the trial. | **Ruled out.** Not free at all for an ongoing deployment ([Fly.io pricing 2026](https://fly.io/docs/about/pricing/)). |
| **Northflank** | Free Sandbox: **2 free services, always-on compute ("no sleeping")**, 2 free cron jobs. | **1 free database addon** included. | **Recommended** — the only one of the four with a genuinely usable, non-time-boxed, currently-available free tier, and the only one that doesn't sleep/scale-to-zero (most predictable behavior for a demo link someone clicks unannounced). See caveats below. |

### Other genuinely suitable alternative considered

**Google Cloud Run + Neon (Postgres) + Cloudflare R2 (storage).** Cloud
Run's Always Free tier (2M requests/month, 360,000 GB-seconds,
180,000 vCPU-seconds — request-based billing means idle time between
requests costs nothing) and Neon's permanent free Postgres (0.5GB, no
credit card, never expires) are both genuinely free forever, arguably more
generously documented than Northflank's. **Not recommended as the primary
pick** because it requires stitching together three separate vendor
consoles/CLIs (GCP + Neon + R2) instead of one platform, which directly
works against this milestone's stated goal — "predictable and reviewable"
— for someone who has never used any of these platforms before. It's
recorded here as a documented fallback if Northflank's free tier turns out
on signup to be inadequate (see §6, Step 0).

### Recommendation: Northflank (compute + database) + Cloudflare R2 (storage)

**Why Northflank over the other three named platforms:** it is the only one
of Railway/Koyeb/Fly.io/Northflank with an actually-usable free tier as of
mid-2026 — the other three are ruled out for concrete, cited reasons above
(exhausted credit, a 5-hour/month database cap plus new-signup lockout, and
no free tier at all, respectively). Its "always-on, no sleeping" compute
matches this project's goal of a **predictable** demo more closely than a
scale-to-zero platform (Koyeb, or Cloud Run without minimum instances) would
— a portfolio visitor's first request never eats a cold-start penalty.

**Why Cloudflare R2 for storage regardless of compute choice:** `STORAGE_BACKEND=s3`
is architecturally mandatory in this codebase (see requirement recap above),
and R2's free tier (10GB storage, 1M Class A + 10M Class B operations/month,
**zero egress fees, no time limit**) is the most generous, best-documented,
permanently-free S3-compatible option available, and this codebase already
explicitly documents R2 as a first-class supported provider (see
`render.yaml`'s own storage comment block and `config/settings.py`'s
`AWS_S3_ENDPOINT_URL` docstring).

**Two honest caveats about Northflank, to verify before creating any
resource (see the checklist's Step 0):**

1. Northflank's own docs state **all users must add a payment method to
   create resources, "regardless of plan selection,"** described as
   identity verification — it will not charge for in-quota Sandbox usage,
   but it is not credit-card-free the way Neon and Cloudflare R2 are.
2. Northflank's public pricing page states "2 free services, 1 free
   database, always-on-compute, no sleeping" but **does not publicly
   document the exact RAM/CPU/storage numbers or any hour-based cap** for
   the Sandbox tier (unlike Railway/Koyeb/Fly.io, whose limits are
   extensively documented specifically because they're restrictive). This
   is a genuine, disclosed gap in available evidence, not a guess dressed
   up as a fact — confirm the actual numbers at signup, before deploying
   anything, per the checklist.

---

## 2. Environment variable audit

Derived directly from `config/settings.py` (every `config(...)` call) and
cross-checked against `render.yaml` (the one other place these are already
enumerated for production) and `backend/entrypoint.sh` /
`apps/core/management/commands/bootstrap_data.py` for the seeding-related
variables. "Demo value" is this plan's recommendation for a Northflank + R2
deployment; "Production value" is what `render.yaml` already sets, included
for comparison only — **`render.yaml` itself is unchanged**.

| Variable | Required? | Default | Demo value | Production value (render.yaml, unchanged) | Secret? |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `DEBUG` | No | `False` | `False` (Demo Mode is not DEBUG) | `False` | No |
| `DEMO_MODE` | **Yes** (to enable Demo Mode) | `False` | `True` | *(unset — stays `False`)* | No |
| `SECRET_KEY` | **Yes** (DEBUG=False fails closed) | none | generate one (see below) | generated, shared via env group | **Yes** |
| `ALLOWED_HOSTS` | **Yes** (default won't match real host) | `localhost,127.0.0.1` | your Northflank service hostname | `scopetrace-api.onrender.com` | No |
| `DATABASE_URL` | **Yes** (DEBUG=False fails closed) | none | Northflank Postgres addon connection string (or Neon URL, `?sslmode=require`) | Render managed Postgres | **Yes** |
| `STORAGE_BACKEND` | **Yes** (DEBUG=False fails closed) | `''` (fails) | `s3` | `s3` | No |
| `AWS_ACCESS_KEY_ID` | **Yes** (with STORAGE_BACKEND=s3) | `''` | R2 API token access key | operator's chosen provider | **Yes** |
| `AWS_SECRET_ACCESS_KEY` | **Yes** (with STORAGE_BACKEND=s3) | `''` | R2 API token secret | operator's chosen provider | **Yes** |
| `AWS_STORAGE_BUCKET_NAME` | **Yes** (with STORAGE_BACKEND=s3) | `''` | your R2 bucket name | operator's chosen provider | No (name only) |
| `AWS_S3_ENDPOINT_URL` | Recommended (blank = real AWS S3) | `''` | `https://<account-id>.r2.cloudflarestorage.com` | operator's chosen provider | No |
| `AWS_S3_REGION_NAME` | No | `auto` | `auto` (R2 default) | operator's chosen provider | No |
| `AWS_S3_ADDRESSING_STYLE` | No | `virtual` | `virtual` (R2 default) | operator's chosen provider | No |
| `AWS_S3_URL_EXPIRE_SECONDS` | No | `3600` | `3600` | *(unset — default)* | No |
| `CORS_ALLOW_ALL_ORIGINS` | No | `False` | `False` | `False` | No |
| `CORS_ALLOWED_ORIGINS` | **Yes** (default is localhost only) | `http://localhost:5173,http://localhost:3000` | your demo frontend's real origin | `https://scopetrace.vercel.app` | No |
| `CSRF_TRUSTED_ORIGINS` | **Yes** (no auto-detect off Render) | `''` | `https://<your-northflank-domain>` | `https://scopetrace.vercel.app` (+ Render auto-appends its own host) | No |
| `REDIS_URL` | No — **not needed in Demo Mode** | `''` | leave unset | Render managed Redis | **Yes** (if set) |
| `AI_ENABLED` | No | `False` | `True` (to actually demo the AI capabilities) | operator's choice | No |
| `AI_PROVIDER` | No | `echo` under `DEMO_MODE=True` as of D5 | leave unset (echo is correct for a demo) | operator's choice | No |
| `AI_PROVIDER_TIMEOUT_SECONDS` | No | `30` under `DEMO_MODE=True` as of D5 | leave unset (30s default) | N/A (production uses the SDK's own default; see `DEMO_MODE_LATENCY.md`) | No |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | No | `''` | leave unset (echo needs neither) | operator's choice | **Yes** (if set) |
| `AI_DEFAULT_MODEL` | No | `claude-sonnet-5` | leave default | leave default | No |
| `AI_DEFAULT_EGRESS_TIER` | No | `REDACTED` | leave default | leave default | No |
| `AI_DEFAULT_MONTHLY_BUDGET_USD` | No | `50.00` | leave default | leave default | No |
| `AI_JUDGE_ENABLED` | No | `False` | leave default | leave default | No |
| `JWT_SIGNING_KEY` | No | falls back to `SECRET_KEY` | leave unset | leave unset | **Yes** (if set) |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | No | `15` / `7` | leave default | leave default | No |
| `EMAIL_HOST` (+ `_PORT`/`_USER`/`_PASSWORD`/`_USE_TLS`) | No | `''` (console backend) | leave unset (no real email needed in a demo) | operator's choice | **Yes** (`_PASSWORD`, if set) |
| `DEFAULT_FROM_EMAIL` | No | `noreply@scopetrace.local` | leave default | `noreply@scopetrace.local` | No |
| `EMAIL_TIMEOUT` | No | `10` | leave default | leave default | No |
| `THROTTLE_ANON` / `_USER` / `_LOGIN` / `_AI` | No | `100/hour` / `2000/hour` / `10/min` / `60/hour` | leave default | leave default | No |
| `LOG_LEVEL` | No | `INFO` | leave default | leave default | No |
| `STALE_BATCH_THRESHOLD_MINUTES` / `FAILED_TASK_LOG_RETENTION_DAYS` / `CELERY_HEARTBEAT_TTL_SECONDS` | No | `30` / `90` / `180` | irrelevant (Beat doesn't run); leave default | leave default | No |
| `SECURE_SSL_REDIRECT` / `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | No | `True` (when DEBUG=False) | leave default (Northflank terminates TLS) | leave default | No |
| `SECURE_HSTS_SECONDS` | No | `31536000` | leave default | leave default | No |
| `BOOTSTRAP_DATA` | No (`entrypoint.sh`) | `false` | `true` (self-seed on first boot) | *(set via `preDeployCommand`, not this var — see below)* | No |
| `BOOTSTRAP_DEMO_USERS` | No (`bootstrap_data` command) | `false` | `true` (seed the 4 demo role logins the frontend already advertises) | not set | No |
| `DEMO_USER_PASSWORD` | **Yes, if `BOOTSTRAP_DEMO_USERS=true`** (DEBUG=False fails closed to skip, not an insecure default) | none | choose one | not set | **Yes** |
| `DJANGO_SUPERUSER_USERNAME` / `_EMAIL` | No | `admin` / `admin@scopetrace.local` | leave default | `admin` / `admin@scopetrace.local` | No |
| `DJANGO_SUPERUSER_PASSWORD` | Recommended (admin login is skipped without it) | none | choose one | set in Render dashboard | **Yes** |
| `RUN_MIGRATIONS` | No (`entrypoint.sh`) | `true` | `true` (this is the only service — it must own migrations) | `false` on api (uses `preDeployCommand` instead); `false` on worker/beat | No |

**Not an env var, but load-bearing:** the container's exposed port. Render's
`api` service does **not** use the Dockerfile at all (`runtime: python`,
its own `buildCommand`/`startCommand` with `--bind 0.0.0.0:$PORT`).
Northflank building from the Dockerfile **will** use it, and the Dockerfile's
`CMD` hardcodes `--bind 0.0.0.0:8000` — it does not read a `$PORT` variable.
**Configure Northflank's service port to `8000`**, matching the Dockerfile's
`EXPOSE 8000`; do not expect a `$PORT` env var to change this.

---

## 3. End-to-end Demo Mode verification

Traced and **executed** (not just reasoned about) via a new permanent test,
[`apps/ingestion/tests_demo_mode.py::DemoModeFullChainTests`](../backend/apps/ingestion/tests_demo_mode.py):

```
Upload (POST /api/upload/sap/)
  -> Ingestion (IngestionService.ingest_batch, inline)
  -> AI anomaly-detection + validation-assistance (generate_*_task, inline, echo provider)
  -> Carbon calculation (CarbonCalculationService.calculate_for_batch, inline)
  -> AI factor-recommendation (generate_factor_recommendations_task, inline)
  -> Dashboard update (GET /api/metrics/summary/ reflects the new batch/total)
```

All in **one HTTP request/response cycle**, asserted directly:

- `UploadBatch.status` and `.calculation_status` are both terminal
  immediately after the upload response returns (not left `QUEUED` for a
  worker that doesn't exist in the test environment).
- A real `AIInteraction` row exists for the `anomaly_detection` capability
  — the AI gateway ran a genuine (echo) provider round trip inline.
- `GET /api/metrics/summary/` — the exact endpoint `DashboardPage`'s KPI
  cards read from — reports `batch_count: 1` and a non-zero
  `total_co2e_tonnes` measured **after** the upload, versus `0`/`"0"`
  measured **before** it, in the same test.

One real bug-shaped finding surfaced and resolved while writing this test:
`apps.carbon.services.metrics_cache.bump_calc_version()` defers its
cache-invalidating write via `transaction.on_commit()` (an existing, Phase
7.5 H3 design choice — see that function's docstring). Confirmed this fires
correctly on a real request (verified using this codebase's own established
`captureOnCommitCallbacks(execute=True)` pattern, already used identically
in `apps/ingestion/tests_soft_delete.py`) — not a Demo Mode defect, just a
`TestCase` transaction-wrapping artifact that had to be worked around
correctly in the test itself.

**Result: the full chain is confirmed to work end-to-end with no Celery
Worker or Beat process, run against the real pipeline code** (not mocked)
— see `backend/apps/ingestion/tests_demo_mode.py` for the executable proof.
Full suite: 943 backend tests pass (`manage.py test`), including this one.

---

## 4. Deployment configuration for Northflank

New, additive files under [`deployment/northflank/`](../deployment/northflank/):
a best-effort `template.json` (Northflank's Infrastructure-as-Code format)
and a `README.md` explaining exactly which fields are verified against
Northflank's documented JSON schema versus which fields (plan/billing IDs)
must be confirmed manually against your account before use — mirroring
`render.yaml`'s own "confidence note" pattern for the same reason: this
project's established rule is to never silently guess unverifiable platform
configuration. **`render.yaml` and `docker-compose.yml` are untouched.**

---

## 5. Manual deployment checklist

**A separate, complete, zero-familiarity checklist is at
[`deployment/northflank/CHECKLIST.md`](../deployment/northflank/CHECKLIST.md).**
It covers: verifying free-tier limits before creating anything, Cloudflare
R2 bucket creation, GitHub connection, Northflank project/service creation,
every environment variable from §2 above, build/start command configuration,
health check path, domain, and post-deployment verification steps (health
endpoints, login, one demo upload, dashboard check).

---

## 6. Stop condition

This milestone stops here. **No Northflank project, no Cloudflare R2
bucket, and no database have been created.** Review §1's platform choice
and §2's environment variable table first; the checklist in
`deployment/northflank/CHECKLIST.md` is written to be followed only after
that review, and even then walks through free-tier verification (its Step
0) before creating a single resource.
