"""
Foundational Celery tasks for apps.core.

Kept intentionally free of business logic — apps.core owns cross-cutting
infrastructure concerns (health, in Phase 5 also task plumbing), not domain
rules. Domain tasks (ingestion, carbon calculation) live in their own apps'
tasks.py modules and call existing services, never embed logic inline.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='apps.core.tasks.ping')
def ping() -> str:
    """Trivial liveness task — proves the broker/worker pipeline end-to-end.

    Used by tests (called eagerly) and, against a running compose stack, via
    `ping.delay().get()` to confirm a real worker picked it up.
    """
    logger.info("apps.core.tasks.ping executed")
    return "pong"
