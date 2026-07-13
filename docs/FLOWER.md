# Flower — Celery Monitoring UI (`FLOWER.md`)

Phase 5h — the last piece of the original "worker health via beat heartbeat +
`/healthz/worker/` (Flower optional, dev-only compose profile)" roadmap
entry. The heartbeat and `/healthz/worker/` extension were already delivered
in Phase 5f (`apps.core.tasks.heartbeat_task`, the `beat_heartbeat` field) —
this milestone is Flower alone.

---

## 0. What it's for, and why it needed no code changes

Flower is a real-time web UI over Celery's own task/worker/broker state:
active/scheduled/reserved tasks per worker, per-queue message counts, task
history with args/kwargs/runtime/result, and worker control (shutdown, pool
resize) — all read from data Celery already publishes internally.

**Zero application code changes were needed to support it**, because the
groundwork was laid back in Phase 5a specifically for this moment:
`CELERY_WORKER_SEND_TASK_EVENTS` / `CELERY_TASK_SEND_SENT_EVENT` have been
`True` since `config/settings.py` was first written (see the comment there
— "zero-cost to enable now so Flower ... work[s] without a config change
when Phase 5h adds them"), and `config/celery.py`'s standard app wiring
needs nothing Flower-specific: it discovers everything through the same
broker/result-backend URLs every other Celery client in this stack already
uses.

---

## 1. Why the official `mher/flower` image, not our own backend image

Flower is a pure operator/developer convenience — it never runs in
production, and doesn't touch `UploadBatch` state, the DB, or any
application code path. Two ways to run it in Docker Compose:

| | Official `mher/flower` image (chosen) | `celery flower` via our own backend image |
|---|---|---|
| `requirements.txt`/`Dockerfile` changes | None | Adds the `flower` PyPI package to the same image `api`/`worker` build from, even though it's never invoked in production |
| Image purity | The production-mirroring backend image stays exactly what it is today | Every image rebuild carries a monitoring tool's dependency tree for no production benefit |
| Precedent | Matches how Postgres/Redis/MinIO are already run — official images for infrastructure, our own image only for application code | — |

**Chosen: `mher/flower:2.0`.** Reads `CELERY_BROKER_URL`/
`CELERY_RESULT_BACKEND` env vars pointed at the same Redis every other
service uses — no different from any other Celery client connecting to the
same broker.

---

## 2. "Optional, dev-only" — Docker Compose `profiles`

```yaml
flower:
  image: mher/flower:2.0
  profiles: ["monitoring"]
  environment:
    CELERY_BROKER_URL: "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: "redis://redis:6379/0"
    FLOWER_PORT: "5555"
    FLOWER_BASIC_AUTH: "${FLOWER_USER:-scopetrace}:${FLOWER_PASSWORD:-scopetrace123}"
  ports:
    - "5555:5555"
  depends_on:
    redis:
      condition: service_healthy
```

A service tagged with a `profiles:` entry is **not** started by a plain
`docker compose up` — it only starts when that profile is explicitly
requested:

```
docker compose --profile monitoring up -d flower
```

This is the mechanism that makes "optional, dev-only" a real guarantee
rather than a convention someone has to remember — there's no way to
accidentally bring Flower up in a production Compose invocation that doesn't
pass `--profile monitoring`.

**Basic auth by default**, not left open — `FLOWER_BASIC_AUTH` defaults to
`scopetrace`/`scopetrace123`, the same "sane default, override via env"
pattern already used for MinIO's root credentials. Flower exposes internal
task/queue state (arguments like `batch_id`/`workflow_id`, worker hostnames,
queue depths) — nothing more sensitive than what's already visible through
the API to an authenticated user, but there's no reason to leave it
unauthenticated either.

---

## 3. Live verification

Two things had to be proven against the real stack, not just read from the
compose file:

1. **A plain `docker compose up` does NOT start Flower** — confirms the
   `profiles` gating actually works, not just that the YAML looks right.
2. **`docker compose --profile monitoring up -d flower` starts it, and it
   shows real data** — the `notifications`/`ingestion`/`calculation`/
   `maintenance`/`celery` queues, the running `worker`, and live task events
   from a real upload dispatched through the actual HTTP API — proving the
   zero-config integration from §0 actually works end-to-end, not just in
   theory.

See the implementation report for this milestone for the actual verification
transcript.
