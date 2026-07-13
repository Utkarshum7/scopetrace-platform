"""
Celery application for ScopeTrace (Phase 5 — async processing).

Standard Django-Celery wiring: this module defines the app; `config/__init__.py`
imports it so `@shared_task` works from any app without manual registration.

Design notes (see docs/PRODUCTION_ENGINEERING.md for the full rationale):
  - ACKS_LATE + prefetch=1: a task is only acknowledged after it completes, so a
    worker that crashes mid-task redelivers it instead of losing it, and adding
    worker replicas actually distributes load evenly (no single worker
    prefetching a queue's worth of work). This requires tasks to be safe to
    re-run if redelivered — an idempotency constraint later Phase 5 milestones
    (async ingestion, calculation) are designed around from the start.
  - Task events, enabled below, are what Flower (Phase 5h — an optional,
    dev-only `docker compose --profile monitoring up flower`, see
    docker-compose.yml) and `celery events` consume for live task/worker
    visibility — enabling them cost nothing before Flower existed to use
    them, and needed no further config change once it did.
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('scopetrace')

# Read CELERY_* keys from Django settings (config.settings.CELERY_BROKER_URL
# becomes app.conf.broker_url, etc).
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in every INSTALLED_APPS app.
app.autodiscover_tasks()
