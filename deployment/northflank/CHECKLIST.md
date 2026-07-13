# Manual Deployment Checklist — ScopeTrace Demo Mode on Northflank

Written for someone who has **never used Northflank or Cloudflare before.**
Follow it top to bottom. **Stop and review with the team before Step 4**
(the first point any billable/persistent cloud resource gets created) if
this is your first time through.

Background reading before you start: [`docs/DEMO_DEPLOYMENT_PLAN.md`](../../docs/DEMO_DEPLOYMENT_PLAN.md)
(why Northflank, why R2, the full environment-variable table) and
[`README.md`](../../README.md)'s "Demo Deployment" section (what Demo Mode
is and why it's safe — it never touches production).

---

## Step 0 — Verify free-tier limits before creating anything

Northflank's public pricing page does not list exact RAM/CPU/storage
numbers for its free Sandbox tier (see `docs/DEMO_DEPLOYMENT_PLAN.md` §1's
caveats). Before proceeding:

1. Go to <https://northflank.com/pricing> and re-read the current Sandbox
   plan description.
2. If anything below has changed since this checklist was written (2026-07),
   stop and re-evaluate — the recommendation in `docs/DEMO_DEPLOYMENT_PLAN.md`
   §1 assumed: 2 free services, 1 free database, always-on compute (no
   sleeping), and a required-but-non-charging payment method.
3. Confirm you're comfortable adding a payment method for identity
   verification (Northflank will not charge you for in-quota Sandbox usage,
   but the card is required to sign up at all).

---

## Step 1 — Create a Cloudflare account and an R2 bucket (storage)

ScopeTrace's `STORAGE_BACKEND=s3` setting is mandatory whenever
`DEBUG=False` — Demo Mode needs a real S3-compatible bucket regardless of
which compute platform you choose.

1. Go to <https://dash.cloudflare.com/sign-up> and create a free account
   (email + password; no credit card required for R2's free tier).
2. In the Cloudflare dashboard, open **R2 Object Storage** from the left
   sidebar.
3. Click **Create bucket**. Name it something like `scopetrace-demo`.
   Region: **Automatic**.
4. Once created, go to **R2 → Manage R2 API Tokens → Create API Token**.
   - Permissions: **Object Read & Write**.
   - Scope it to the bucket you just created (not account-wide, if offered).
   - Save the generated **Access Key ID** and **Secret Access Key**
     somewhere safe — the secret is shown only once.
5. On the bucket's **Settings** page, copy the **S3 API endpoint** shown
   (it looks like `https://<account-id>.r2.cloudflarestorage.com`) — this
   is your `AWS_S3_ENDPOINT_URL`.

You now have: bucket name, endpoint URL, access key ID, secret access key.

---

## Step 2 — Create a Northflank account and project

1. Go to <https://northflank.com> and sign up (GitHub sign-in is the
   fastest path and also sets up the GitHub connection in one step).
2. If prompted, add a payment method (see Step 0 — required for
   verification, will not be charged for in-quota usage).
3. Click **Create new project**. Name it `scopetrace-demo`. Leave other
   settings at their defaults.

---

## Step 3 — Connect the GitHub repository

1. Inside the new project, click **Create new service** → **Combined
   service** (a service Northflank both builds and runs — the right choice
   for a Dockerfile-based app).
2. Under **Source**, choose **GitHub**. If this is your first time,
   Northflank will prompt you to install its GitHub App — grant it access
   to `Utkarshum7/scopetrace-platform` (or your own fork) specifically, not
   every repository on the account, if that option is offered.
3. Select the repository and the branch you want to deploy (`main`, or a
   dedicated demo branch if you prefer to control exactly what's live
   independently of ongoing work on `main`).

---

## Step 4 — Configure the build (this is where a resource starts getting created)

1. **Build type**: choose **Dockerfile**.
2. **Dockerfile location**: `/backend/Dockerfile` — **with the leading
   slash**. Northflank resolves this path relative to the repo root, and
   its own docs/examples always show a leading slash (`/Dockerfile`,
   `/app/src`); omitting it causes a real, reproducible "No Dockerfile
   found at this location" error (confirmed during D10's live deployment).
3. **Build context**: `/backend` — also with the leading slash, same
   reason. This scopes which files are available to `COPY`/`ADD`
   instructions in the Dockerfile.
4. There is no separate "working directory" or "root directory" field
   distinct from build context on Northflank — don't look for one.
5. **Start command**: leave blank/unset. `backend/Dockerfile` already
   defines its own `ENTRYPOINT`/`CMD` (`entrypoint.sh` -> gunicorn);
   Northflank should use that, not an override.
6. Leave build arguments empty — nothing in `backend/Dockerfile` needs any.

---

## Step 5 — Configure networking and health check

1. **Port**: `8000`. (`backend/Dockerfile`'s `CMD` hardcodes
   `--bind 0.0.0.0:8000` — it does **not** read a `$PORT` variable the way
   Render's separate, Dockerfile-independent `startCommand` does. If you
   set anything other than `8000` here, the service will not be reachable.)
2. **Protocol**: HTTP, public: yes (so it gets a public HTTPS URL).
3. **Health check**: HTTP, path `/healthz`, port `8000`. This is
   ScopeTrace's real database-aware health endpoint (see
   `backend/apps/core/views.py`) — it returns 503 if the database is
   unreachable, so Northflank won't route traffic to a broken instance.

---

## Step 6 — Add the PostgreSQL database

1. Still inside the `scopetrace-demo` project, click **Create new** →
   **Database** (Addon) → **PostgreSQL**.
2. Choose the free Sandbox plan (confirm it's genuinely free per Step 0).
3. Once created, open the database's **Connection** tab and copy the full
   connection string (`postgres://user:password@host:port/dbname`). This
   is your `DATABASE_URL`.

---

## Step 7 — Set every environment variable

Back on the web service, open its **Environment** tab. Add each variable
below. Mark anything in the "Secret?" column as a **Secret** (Northflank
encrypts these and hides them from logs), not a plain runtime variable.

Full rationale for every value is in `docs/DEMO_DEPLOYMENT_PLAN.md` §2 —
this is the condensed action list:

| Variable | Value | Secret? |
| :--- | :--- | :--- |
| `DEBUG` | `False` | No |
| `DEMO_MODE` | `True` | No |
| `SECRET_KEY` | Generate: `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"` | **Yes** |
| `ALLOWED_HOSTS` | The hostname Northflank assigned your service (visible on the service's overview page once created, e.g. `scopetrace-demo-api--xxxxx.code.run`) | No |
| `DATABASE_URL` | From Step 6 | **Yes** |
| `STORAGE_BACKEND` | `s3` | No |
| `AWS_ACCESS_KEY_ID` | From Step 1 | **Yes** |
| `AWS_SECRET_ACCESS_KEY` | From Step 1 | **Yes** |
| `AWS_STORAGE_BUCKET_NAME` | From Step 1 (e.g. `scopetrace-demo`) | No |
| `AWS_S3_ENDPOINT_URL` | From Step 1 | No |
| `AWS_S3_REGION_NAME` | `auto` | No |
| `AWS_S3_ADDRESSING_STYLE` | `virtual` | No |
| `CORS_ALLOWED_ORIGINS` | The real origin of wherever the demo frontend is hosted | No |
| `CSRF_TRUSTED_ORIGINS` | `https://` + the same hostname as `ALLOWED_HOSTS` | No |
| `AI_ENABLED` | `True` (to actually show off the AI capabilities) | No |
| `BOOTSTRAP_DATA` | `true` | No |
| `BOOTSTRAP_DEMO_USERS` | `true` | No |
| `DEMO_USER_PASSWORD` | Choose a password (this is what the `orgadmin`/`analyst`/`auditor`/`viewer` demo logins will use) | **Yes** |
| `DJANGO_SUPERUSER_USERNAME` | `admin` | No |
| `DJANGO_SUPERUSER_EMAIL` | `admin@scopetrace.local` (or your own) | No |
| `DJANGO_SUPERUSER_PASSWORD` | Choose a password | **Yes** |
| `RUN_MIGRATIONS` | `true` | No |

Do **not** set `REDIS_URL`, `AI_PROVIDER`, or `AI_PROVIDER_TIMEOUT_SECONDS`
— all three already default correctly under `DEMO_MODE=True` (see the
Demo Deployment Plan §2).

---

## Step 8 — Deploy

1. Save the environment variables, then trigger the first deployment
   (Northflank does this automatically once the service and its variables
   are configured — check the **Builds** tab for progress).
2. Watch the build logs. `backend/entrypoint.sh` will run migrations,
   collect static files, and (because `BOOTSTRAP_DATA=true` and
   `BOOTSTRAP_DEMO_USERS=true`) seed the demo organization, data sources,
   admin user, and the four demo role logins — all on this first boot,
   automatically.

---

## Step 9 — Post-deployment verification

Once the build succeeds and the service shows "Running":

1. **Health check**: open `https://<your-service-hostname>/healthz` in a
   browser. Expect `{"status": "ok", "database": "ok"}`.
2. **Worker health (demo-aware)**: open `/healthz/worker/`. Expect
   `{"status": "ok", "mode": "demo", "demo_mode": true, ...}` — **not** a
   503. This confirms `DEMO_MODE` actually took effect.
3. **Admin login**: go to `/admin/` and sign in with the
   `DJANGO_SUPERUSER_USERNAME`/`_PASSWORD` you set in Step 7.
4. **API login**: from wherever the frontend is pointed at this backend
   (`VITE_API_URL`), sign in as `analyst` / your `DEMO_USER_PASSWORD`.
5. **One real upload**: upload a sample CSV from `sample_data/` through the
   Upload Center. Confirm the response comes back `COMPLETED` (not
   `QUEUED`) — proof the synchronous Demo Mode pipeline ran.
6. **Dashboard check**: open the Dashboard and confirm the KPI cards
   reflect the upload you just did (non-zero CO2e, updated batch count).
7. **AI check** (if `AI_ENABLED=True`): open a record from that upload and
   confirm any AI advisory annotation is visible, clearly labeled
   "AI Advisory."

If all seven pass, the Demo Mode deployment is verified working.

---

## Rollback / cleanup

Everything created in this checklist lives entirely in your own Northflank
project and Cloudflare account — deleting the Northflank project (Project
Settings → Delete) and the R2 bucket removes it all. Nothing here can
affect the production Render/Vercel deployment; they share no
infrastructure, only the same GitHub repository as a read-only source.
