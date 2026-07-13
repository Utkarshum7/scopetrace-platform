# Northflank deployment artifacts (D7 — Demo Mode preparation)

**Nothing in this directory has been deployed.** These are reviewable
preparation artifacts only. See [`docs/DEMO_DEPLOYMENT_PLAN.md`](../../docs/DEMO_DEPLOYMENT_PLAN.md)
for the full platform evaluation and environment-variable audit behind these
files, and [`CHECKLIST.md`](CHECKLIST.md) for the actual step-by-step manual
deployment procedure — **that checklist, not `template.json`, is the
recommended way to deploy for the first time.**

## Files

- **`CHECKLIST.md`** — the primary artifact. A complete, zero-familiarity,
  step-by-step manual walkthrough (GitHub connection → project creation →
  environment variables → build/start command → domain → health check →
  post-deployment verification). Use this first.
- **`template.json`** — an optional, secondary Infrastructure-as-Code
  reference in Northflank's own template format, for repeatable/scripted
  redeployment *after* you've already deployed manually once via the
  checklist and know your account's real values for the placeholders below.

## Confidence note (read before using `template.json`)

Following this project's established rule (see `render.yaml`'s own
"CONFIDENCE NOTE" header) to never silently guess unverifiable platform
configuration, this file's structure is split by confidence:

**HIGH CONFIDENCE — verified against Northflank's documented template
schema** (`apiVersion`, `Workflow`/`Project`/`Addon`/`CombinedService`/
`SecretGroup` node kinds, `vcsData`, `buildSettings.dockerfile`,
`runtimeEnvironment`, `ports`, `healthChecks`, addon `${refs...}`
interpolation): these fields are correctly shaped per Northflank's
"Write a template" documentation.

**MUST BE CONFIRMED BEFORE USE — Northflank's Sandbox/free-tier plan IDs
are not publicly documented.** Every `REPLACE_WITH_...` placeholder in
`template.json` needs a real value from your own account:

| Placeholder | Where to find the real value |
| :--- | :--- |
| `REPLACE_WITH_YOUR_ACCOUNT_FREE_TIER_DB_PLAN_ID` | Northflank dashboard → create a database manually once → note which `deploymentPlan` slug the free Sandbox tier actually assigns |
| `REPLACE_WITH_YOUR_ACCOUNT_FREE_TIER_SERVICE_PLAN_ID` | Same, for a combined service |
| `REPLACE_WITH_YOUR_DEPLOY_BRANCH` | Whichever branch you want Northflank to track (e.g. `main`) |
| `REPLACE_WITH_YOUR_NORTHFLANK_SERVICE_HOSTNAME` | Assigned after the service is first created (Northflank shows it in the service's overview page) |
| `REPLACE_WITH_YOUR_DEMO_FRONTEND_ORIGIN` | Wherever the demo frontend ends up hosted (e.g. a Vercel preview URL) |
| `REPLACE_WITH_YOUR_NORTHFLANK_SERVICE_HTTPS_ORIGIN` | Same as the service hostname, with `https://` |
| `REPLACE_WITH_YOUR_R2_*` | Your Cloudflare R2 bucket + API token (see `CHECKLIST.md` Step 2) |
| `REPLACE_WITH_A_GENERATED_DJANGO_SECRET_KEY` | Generate locally: `python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"` — never reuse the production `SECRET_KEY` |
| `REPLACE_WITH_A_CHOSEN_*_PASSWORD` | Your own choice, kept out of git |

Also confirmed by reading `backend/Dockerfile` directly (not guessed): its
`CMD` hardcodes `--bind 0.0.0.0:8000` — it does not read a `$PORT`
variable the way `render.yaml`'s own (Dockerfile-independent) `startCommand`
does. `template.json`'s `ports.internalPort` and `healthChecks.http.port`
are both set to `8000` to match; if you change the Dockerfile's bind port,
update both here too.

## What this does NOT change

`render.yaml`, `docker-compose.yml`, and every other production deployment
artifact are completely untouched. This directory is purely additive, the
same pattern this project used previously for the (since-removed, abandoned)
Oracle Cloud exploration — the difference is this one is a live, reviewed
plan, not an abandoned one.
