"""
Phase 7.5 (H1, Finding 1) -- deployment/config drift guard.

Every Celery queue that a task is ROUTED to (config.settings.
CELERY_TASK_ROUTES) plus the default queue MUST be CONSUMED by a worker
`-Q` list in every deployment descriptor. A queue with no consumer
silently strands its tasks in the broker forever, with no error anywhere
-- exactly the production incident this milestone fixes (render.yaml's
worker omitted the 'ai' queue while docker-compose.yml consumed it).

This is a pure text/config assertion -- no DB, no Celery broker, no
network. It reads the two deployment files as plain text (PyYAML is not a
project dependency) and extracts each worker's `-Q` list by regex, which
is robust to both docker-compose.yml's JSON-array command form and
render.yaml's shell-string startCommand form.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# backend/apps/tasks/tests_queue_coverage.py -> repo root is parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RENDER_YAML = _REPO_ROOT / "render.yaml"
_DOCKER_COMPOSE = _REPO_ROOT / "docker-compose.yml"

# Matches an actual Celery worker invocation's `-Q` list: `worker`, then
# (on the same line) `-Q`, then the queue list. Requiring `worker` before
# `-Q` keeps prose that merely mentions the flag -- e.g. the comment
# "-Q lists every queue explicitly" or "add a `-Q <queue>` worker service"
# -- from being mistaken for a real consumer list. The separators between
# `-Q` and its value absorb both render.yaml's shell form (`-Q celery,...`)
# and docker-compose.yml's JSON-array form (`"-Q", "celery,..."`).
#   render.yaml:      ... worker --loglevel=info -Q celery,ingestion,ai
#   docker-compose:   "worker", "--loglevel=info", "-Q", "celery,ingestion,ai"
_Q_FLAG_RE = re.compile(r"worker[^\n]*?-Q[\"'\s,]+([a-z][a-z_,]*)")


def _required_queues() -> set[str]:
    routed = {route["queue"] for route in settings.CELERY_TASK_ROUTES.values()}
    routed.add(settings.CELERY_TASK_DEFAULT_QUEUE)
    return routed


def _consumed_queues(descriptor_text: str) -> set[str]:
    """Union of every worker `-Q` list found in the file -- correct even if
    a future deployment splits queues across several dedicated worker
    services, since collectively they must still cover every queue."""
    consumed: set[str] = set()
    for match in _Q_FLAG_RE.finditer(descriptor_text):
        consumed.update(q for q in match.group(1).split(",") if q)
    return consumed


class QueueCoverageTests(SimpleTestCase):
    def test_render_yaml_consumes_every_routed_queue(self):
        text = _RENDER_YAML.read_text(encoding="utf-8")
        consumed = _consumed_queues(text)
        self.assertTrue(consumed, "no worker `-Q` list found in render.yaml")
        missing = _required_queues() - consumed
        self.assertFalse(
            missing,
            f"render.yaml worker(s) do not consume routed queue(s) {sorted(missing)} "
            f"-- their tasks would be stranded in the broker. Consumed: {sorted(consumed)}",
        )

    def test_docker_compose_consumes_every_routed_queue(self):
        text = _DOCKER_COMPOSE.read_text(encoding="utf-8")
        consumed = _consumed_queues(text)
        self.assertTrue(consumed, "no worker `-Q` list found in docker-compose.yml")
        missing = _required_queues() - consumed
        self.assertFalse(
            missing,
            f"docker-compose.yml worker(s) do not consume routed queue(s) {sorted(missing)} "
            f"-- their tasks would be stranded in the broker. Consumed: {sorted(consumed)}",
        )

    def test_the_ai_queue_specifically_is_covered_everywhere(self):
        # Regression pin for the exact H1 finding: the 'ai' queue existed in
        # CELERY_TASK_ROUTES but render.yaml's worker never consumed it.
        self.assertIn("ai", _required_queues())
        for descriptor in (_RENDER_YAML, _DOCKER_COMPOSE):
            consumed = _consumed_queues(descriptor.read_text(encoding="utf-8"))
            self.assertIn("ai", consumed, f"'ai' queue not consumed by any worker in {descriptor.name}")
