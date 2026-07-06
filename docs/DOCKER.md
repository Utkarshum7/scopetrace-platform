# Docker Image & Compose Architecture (`DOCKER.md`)

Phase 5j — multi-stage backend build and a review of whether `worker`/`beat`
should become optional Compose profiles. Builds on 5a-5h's Compose services
(db, redis, minio, api, worker, beat, flower) and 5i's CI Docker-build
verification — neither was redesigned, only the backend `Dockerfile` and
`.dockerignore`.

---

## 0. Pre-implementation review

**The backend `Dockerfile` was single-stage** (`FROM python:3.12-slim`,
install `requirements.txt`, `COPY . .`, drop to non-root). `psycopg2-binary`
is the only remotely "compiled" dependency this project has, and it ships
precompiled wheels — so there's no build-toolchain (gcc, headers) to strip
out in a second stage, which is multi-stage's most common motivation.
Frontend's `Dockerfile` has been multi-stage (Node build → nginx serve)
since Phase 0 already — nothing to change there.

**A concrete problem found by actually reading `.dockerignore`, not
assumed:** it excluded `venv/`, `__pycache__/`, `.git/`, `db.sqlite3`,
`staticfiles/` — but **not** `venv_check/`, the throwaway verification venv
this whole session has repeatedly created directly under `backend/`. A
`docker build` run from a working tree with `venv_check/` present would
have copied an entire redundant Python virtualenv straight into the image
via the blanket `COPY . .`. Also missing: `.ruff_cache/`, `media/` (local
dev's `LocalFileSystemStorageService` files — never needed at runtime, since
production/Compose both use the S3-compatible provider), `requirements-dev.txt`
and `pyproject.toml` (Phase 5i's CI-only lint/security tooling).

---

## 1. Decision made with you before implementing: keep `worker`/`beat` in the default set

The roadmap entry for this milestone included "compose profiles for
worker/beat." Docker Compose profiles are fundamentally **opt-in**: a
service with no `profiles:` key always starts on a plain `docker compose
up`; one *with* a `profiles:` key only starts when that profile is
explicitly requested (exactly Flower's `monitoring` profile since Phase
5h). Applying that same pattern to `worker`/`beat` would mean a bare `docker
compose up` **no longer starts async processing at all** — every upload
would sit in `QUEUED` forever with nothing to say why, silently breaking
the quick-start documented at the top of `docker-compose.yml` and relied on
in the live verification of every milestone since 5a. Unlike Flower
(observability, genuinely optional), Celery has been core, load-bearing
architecture since Phase 5b.

**Chosen: leave `worker`/`beat` unprofiled** — `docker compose up` keeps
working exactly as documented. If a genuine multi-host/split-tier
deployment topology (web tier vs. worker tier on separate machines) is ever
actually needed, that's better served by a separate Compose override file
(`docker compose -f docker-compose.yml -f docker-compose.worker-only.yml
up`) than by fighting Compose's opt-in-only profile semantics — not built
now, since there's no concrete deployment target asking for it yet (this
project explicitly runs on Render + Docker Compose, having already rejected
Kubernetes-style multi-node orchestration as unused ceremony misaligned
with its actual deploy targets).

---

## 2. Multi-stage `Dockerfile`

```dockerfile
FROM python:3.12-slim AS deps
...
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim AS runtime
...
COPY --from=deps /root/.local /root/.local
COPY apps/ ./apps/
COPY config/ ./config/
COPY manage.py entrypoint.sh requirements.txt ./
```

**The real, structural benefit here isn't a smaller image from stripped
build tools** (there are none to strip) — it's the **explicit COPY
allow-list** in the `runtime` stage. `COPY . .` would silently pull in
whatever happens to exist in the local build context at build time,
regardless of whether `.dockerignore` correctly excludes it; naming exactly
`apps/`, `config/`, `manage.py`, `entrypoint.sh` structurally can't leak
anything it doesn't name, even if a future dev artifact is forgotten in
`.dockerignore` again. `.dockerignore` was still fixed too (§0) — belt and
suspenders: it also shrinks what gets sent to the Docker daemon as build
context in the first place, independent of what the final COPY list
includes.

### A real bug found and fixed while verifying this locally

`pip install --user` in the `deps` stage installs into `/root/.local`
(root's home, since that stage never switches user). Copying that tree into
`runtime` and setting `PATH=/root/.local/bin:$PATH` covers *finding the
CLI scripts* (gunicorn, celery, etc.), but Python's own import-time
site-packages lookup (`site.getusersitepackages()`) resolves via `$HOME` —
so `runtime`'s `ENV HOME=/root` is what makes `appuser`'s Python still find
the installed packages at `/root/.local/lib/python3.12/site-packages`
instead of looking under a nonexistent `/home/appuser/.local`.

That alone wasn't sufficient, though: `/root` is `700` (root-only) by
default, so even with `HOME` correctly pointed there, non-root `appuser`
still couldn't *traverse into* the directory at all — confirmed by actually
running the built image as `appuser` and hitting `ModuleNotFoundError: No
module named 'django'` despite the path being ostensibly correct. Fixed with
`chmod -R a+rX /root` — read + (for directories) execute/search for every
user, without changing ownership or granting write access `appuser` doesn't
need. Verified afterward: `appuser` can import Django/Celery/psycopg2/
gunicorn cleanly, and a real end-to-end upload through the full Compose
stack (built from this image) completed successfully.

---

## 3. `.dockerignore`

```
venv/
venv_check/
**/__pycache__/
*.pyc
*.pyo
.ruff_cache/
db.sqlite3
db.sqlite3-journal
staticfiles/
media/
.env
.env.example
.git/
.gitignore
requirements-dev.txt
pyproject.toml
```

---

## 4. Verification

- `docker build ./backend` succeeds; running the built image as `appuser`
  successfully imports Django/Celery/psycopg2/gunicorn, and `ls /app`
  confirms none of `venv_check/`, `.ruff_cache/`, `pyproject.toml`,
  `requirements-dev.txt` are present in the final image.
- Full `docker compose up --build` (no profile flags) — every service,
  **including `worker` and `beat` with no special flags**, came up healthy,
  confirming the default quick-start is unchanged.
- A real upload through the live HTTP API (`POST /api/upload/sap/`)
  completed the full `ingest_task` → `calculate_task` →
  `send_notification_task` chain against the rebuilt image —
  `COMPLETED`/`CALCULATED` in under a second, notification email logged via
  the console backend, `beat` still dispatching its scheduled tasks
  normally.
