"""
D4 (Demo Mode) — pure resolution of Celery's synchronous-execution settings
from the deployment mode. Kept as a standalone, side-effect-free function so
the mode matrix is unit-testable without importing or reloading Django
settings.

Two deployment modes (see README "Demo Deployment"):
  * Production (DEMO_MODE=False, the default) — the enterprise architecture:
    Celery Worker + Beat + Redis, tasks dispatched asynchronously via
    ``.delay()``. Eager execution is used ONLY under DEBUG or the test runner,
    exactly as it was before Demo Mode existed.
  * Demo (DEMO_MODE=True) — for free hosting with no background workers: tasks
    run synchronously in-process (``CELERY_TASK_ALWAYS_EAGER``) so no Worker,
    Beat, or broker is required.

``CELERY_TASK_EAGER_PROPAGATES`` is the subtle part, chosen by analysing
production semantics rather than preference:

  In production, ``.delay()`` is fire-and-forget — the HTTP request returns
  before the task runs, and a task failure records its own outcome (batch
  status, dead-letter log, AIInteraction) out of band; it NEVER surfaces as an
  exception to the caller. ``eager_propagates=False`` reproduces that contract
  (the eager call site does not re-raise), so Demo Mode behaves like production
  async. ``eager_propagates=True`` would re-raise a task failure into the
  caller — a behaviour production async cannot produce. Non-demo keeps ``True``
  (the test harness relies on failures surfacing synchronously; DEMO_MODE is
  off under the test runner, so the pre-existing default is preserved exactly).
"""


def resolve_celery_execution(*, debug: bool, testing: bool, demo_mode: bool) -> tuple[bool, bool]:
    """Return ``(task_always_eager, task_eager_propagates)`` for the mode.

    These are DEFAULTS only — ``config.settings`` still lets an explicit
    ``CELERY_TASK_ALWAYS_EAGER`` / ``CELERY_TASK_EAGER_PROPAGATES`` environment
    variable override them on top.
    """
    task_always_eager = bool(debug or testing or demo_mode)
    # Demo Mode mirrors production's fire-and-forget .delay() contract (task
    # failures do not reach the caller); every other context keeps the
    # pre-Demo-Mode default of propagating (True).
    task_eager_propagates = not demo_mode
    return task_always_eager, task_eager_propagates
