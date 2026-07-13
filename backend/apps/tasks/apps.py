from django.apps import AppConfig


class TasksConfig(AppConfig):
    """Cross-cutting task/job observability infra (Phase 5e) — distinct from
    apps.ingestion/apps.carbon, which own the actual business-logic tasks.
    This app owns nothing about WHAT a task does, only what happens when one
    fails permanently (the dead-letter log)."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.tasks'
    verbose_name = 'Task Observability'

    def ready(self):
        # Connects the task_failure signal handler. Must happen at app
        # startup (Django's ready() is the guaranteed-once hook for this),
        # not at import time of signals.py itself, since Celery may import
        # task modules before Django's app registry has fully loaded.
        from apps.tasks import signals

        signals.register_dead_letter_handler()
