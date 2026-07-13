# Continuous Integration (`CI_CD.md`)

Phase 5i — GitHub Actions CI covering backend tests, frontend build/lint,
Docker image build verification, dependency caching, and advisory security
scanning. CI **validates only** — it never deploys. Render deploys
independently via its own git-connected webhook (`render.yaml`), entirely
decoupled from anything in `.github/workflows/`.

---

## 0. Pre-implementation review

**Before this milestone, `.github/` didn't exist at all** — a clean slate,
confirmed by checking the repo, not assumed.

**Two real findings from actually running the tools against the current
code**, not from assumption:

- `ruff check` (no prior Python linter existed) found only **5** pre-existing
  issues, all trivial unused-imports, all auto-fixable. Fixed as part of
  this milestone (see §2) — trivial enough that blocking CI on them from day
  one was clearly the right call.
- `npm run lint` found **107** pre-existing problems. 103 were
  `react/prop-types` — and the `prop-types` package isn't even a dependency
  of this project, confirming the rule was inherited from the default Vite
  template and never actually followed, not a deliberate standard being
  violated. The rest were ~4 dead `import React` statements (obsolete under
  the modern JSX transform this project already uses via
  `@vitejs/plugin-react`) and one `react-hooks/exhaustive-deps` warning.
- `npm audit --audit-level=high` found **4** existing vulnerabilities (2
  moderate, 2 high) — all in dev/transitive dependencies (`esbuild`/`vite`
  dev-server-only issue, `form-data`, `js-yaml`), none in application code
  actually shipped to users.

**A real technical wrinkle, resolved without adding infrastructure:**
`STORAGE_BACKEND` fails closed to `'s3'` whenever `DEBUG=False`, but
`DATABASE_URL`'s sqlite-fallback is *also* gated on `DEBUG` (not on
`_TESTING`). Running CI with `DEBUG=False` (to mirror Docker Compose's
production-like posture) would have forced a MinIO-in-CI dependency just to
satisfy `STORAGE_BACKEND`, adding complexity the roadmap never asked for.
Resolved by running tests with `DEBUG=True` — this keeps `STORAGE_BACKEND`
at its `'local'` default (exactly like every local/`venv_check` test run all
session; `MEDIA_ROOT` is already gated to a temp dir under `_TESTING`) while
still explicitly overriding `DATABASE_URL`/`REDIS_URL` to point at the real
Postgres/Redis service containers below — the actual fidelity improvement
the roadmap wanted, without a needless new moving part.

---

## 1. Two decisions made with you before implementing

### 1.1 Frontend lint: clean up, then make it blocking

Chosen over leaving the 107-problem backlog and running lint
non-blocking. `react/prop-types` is disabled in `eslint.config.js` (not
silencing a followed standard — see the finding above), the ~4 dead
`import React` statements were removed, leaving `npm run lint` at **0
errors** (3 warnings remain — `react-refresh/only-export-components` ×2,
`react-hooks/exhaustive-deps` ×1 — ESLint's default exit code only fails on
errors, so these don't block the build). `frontend-ci.yml`'s `lint` job runs
`npm run lint` with no `continue-on-error` — a real, enforced gate from day
one, not a permanently-ignored report nobody reads.

### 1.2 Security scanning: advisory, not blocking

Both `pip-audit` (backend) and `npm audit` (frontend) run with
`continue-on-error: true` — full output always visible in the job log, but
a finding doesn't fail the run. Chosen because pre-existing findings already
exist (§0) and forcing them to block immediately would make CI red for
reasons unrelated to whatever change actually triggered a given run, before
anyone has evaluated whether each fix is safe to make (a dependency bump can
itself introduce breakage — its own testing burden, not something to force
through silently as a side effect of adding CI). This is also the standard
industry default: scanning starts advisory, flips to blocking once there's
an actual remediation plan for the current backlog.

### 1.3 Addendum (Phase 6h): frontend `test` job, blocking

The Phase 6 architecture review found a real frontend/backend contract
drift (the approval modal called an endpoint the backend no longer accepted
for a Draft record). Phase 6h's H2 milestone added a Vitest + React Testing
Library foundation specifically to catch this class of bug, so
`frontend-ci.yml`'s new `test` job runs `npm run test` with no
`continue-on-error` — same rationale as §1.1's `lint` job: this check exists
to fail the build, not to produce a report nobody reads.

---

## 2. Three modular workflows, not one

| Workflow | Jobs (run in parallel) | Why separate from the others |
|---|---|---|
| `backend-ci.yml` | `test` (Postgres+Redis services), `lint` (ruff), `security` (pip-audit) | Only backend changes need Python tooling; keeping it its own file means its status/logs are never mixed with frontend or Docker output in the Actions UI |
| `frontend-ci.yml` | `build`, `test` (vitest), `lint` (eslint), `security` (npm audit) | Independent toolchain (Node/npm) — no reason to share a workflow file with Python jobs |
| `docker-build.yml` | `backend-image`, `frontend-image` | Verifies both `Dockerfile`s stay buildable; deliberately never pushes anywhere (no registry credentials exist in any of these workflows) |

Each workflow's jobs (`test`/`lint`/`security`, or `backend-image`/
`frontend-image`) run **in parallel** by default — GitHub Actions runs
jobs within one workflow concurrently unless a `needs:` dependency is
declared, and none is here, since none of these checks depends on another's
result.

**No path filtering** (e.g. "only run `frontend-ci.yml` when `frontend/**`
changed) — deliberately, for now. It would save CI minutes, but this is a
personal/portfolio-scale project (low CI volume) where the risk of a path
filter silently skipping a check it shouldn't (e.g. a root-level config
change that affects both sides) outweighs the minor cost savings. A
reasonable future optimization if CI volume ever grows, not a default to
reach for speculatively.

---

## 3. Triggers

```yaml
on:
  push:
    branches: ["**"]
  pull_request:
  workflow_dispatch:
```

`push: branches: ["**"]` (not just `main`) is deliberate: this project's
actual workflow is long-lived phase branches pushed directly (this entire
Phase 5 arc has lived on `phase-5-production-engineering`), not one PR per
commit. Gating only on `pull_request` would mean CI never ran at all under
how this repo is actually used. `pull_request` is included too for whenever
a PR *is* opened, and `workflow_dispatch` allows a manual re-run from the
Actions UI (e.g. to re-check after a flaky external dependency).

Each workflow also has a `concurrency` group keyed on the branch ref with
`cancel-in-progress: true` — a rapid sequence of pushes to the same branch
cancels the now-superseded earlier run instead of queuing redundant ones.

---

## 4. Dependency caching

- **Python**: `actions/setup-python`'s built-in `cache: 'pip'`, keyed on
  `requirements.txt` (`test`) or both `requirements.txt` +
  `requirements-dev.txt` (`security`) / just `requirements-dev.txt` (`lint`)
  — cache invalidates automatically the moment either file's hash changes.
- **Node**: `actions/setup-node`'s built-in `cache: 'npm'`, keyed on
  `package-lock.json`.
- **Docker layers**: `docker/build-push-action`'s `cache-from`/`cache-to:
  type=gha` — reuses unchanged image layers (e.g. the `pip install`/`npm ci`
  layers when only application source changed) across runs via GitHub
  Actions' own cache backend.

---

## 5. `requirements-dev.txt` — CI/dev tooling kept out of the production image

```
ruff==0.15.20
pip-audit==2.10.1
```

Neither is ever imported by application code or needed at runtime. Keeping
them in a separate file (not appended to `requirements.txt`, which
`api`/`worker`/`beat` all build their image from) means a lint or security
tool's own dependency tree never bloats what actually ships — the same
reasoning already applied to Flower in Phase 5h (official image, not our
own backend image).

`backend/pyproject.toml`'s `[tool.ruff]` section is checked in (not relying
on CLI flags anyone could forget to pass) and deliberately scoped to
`select = ["E4", "E7", "E9", "F"]` — pyflakes-equivalent checks only
(unused imports/variables, undefined names, syntax errors), not a wider
pycodestyle/import-sorting rule set. This project had zero linting before
this milestone; starting with the highest-signal, lowest-noise rule set is
what let `ruff check` land as a **blocking** gate on day one with only 5
pre-existing (trivial, fixed) issues, rather than the hundreds a stricter
preset would likely have surfaced — same reasoning as the frontend
`react/prop-types` finding. Broadening the rule set later is a reasonable
increment, not something to reach for speculatively now.

---

## 6. Branch safety — CI verifies, it never releases

- No workflow has a `deploy`, `push`-to-registry, or Render-API step.
- No workflow requires any secret — `pip-audit`/`npm audit` query public
  vulnerability databases anonymously, `docker/build-push-action` runs with
  `push: false`.
- Render's own deploy trigger (its dashboard-configured branch watch on this
  same GitHub repo) is entirely independent of these workflows — adding CI
  here changes nothing about when or how Render deploys.
- **Recommended manual follow-up** (a GitHub repo *setting*, not a code
  change — deliberately not done automatically here): once comfortable with
  these workflows' behavior, consider enabling branch protection on `main`
  requiring `Backend CI`, `Frontend CI`, and `Docker Build Verification` to
  pass before a merge is allowed.

---

## 7. README badges

```markdown
[![Backend CI](.../backend-ci.yml/badge.svg)](...)
[![Frontend CI](.../frontend-ci.yml/badge.svg)](...)
[![Docker Build Verification](.../docker-build.yml/badge.svg)](...)
```

GitHub's built-in workflow badge endpoint reflects the default branch
(`main`)'s latest run status — standard convention, not specific to this
project.
