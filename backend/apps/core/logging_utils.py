"""
Phase 9b -- shared logging configuration helpers, referenced only from
config.settings.LOGGING (via dictConfig's '()' custom-factory syntax) and
from apps.core.middleware.RequestIDMiddleware, which owns `request_id_ctx`'s
one and only .set() call.

Kept separate from apps.core.middleware: this module has no Django-request
dependency (a bare contextvars.ContextVar + a stdlib logging.Filter/
Formatter), so it stays trivially unit-testable and reusable from a
non-HTTP context (a management command, a shell) without importing
anything request-shaped.
"""
import contextvars
import logging
import time

# Default "-" (not "", not None) so the 'verbose' formatter's [{request_id}]
# renders as a visible placeholder, not a blank pair of brackets, for every
# log line emitted outside an HTTP request/response cycle -- a Celery task,
# a management command, `manage.py shell`. Celery tasks already thread their
# own correlation id (UploadBatch.workflow_id) through every log line they
# emit (see apps.ingestion.tasks/apps.carbon.tasks); "-" here is intentional
# and NOT a gap -- see docs/OPERATIONS_RUNBOOK.md's logging section.
request_id_ctx: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIDLogFilter(logging.Filter):
    """Attaches the active request's correlation id (or "-") to every
    LogRecord as `record.request_id`, for the 'verbose' formatter's
    %(request_id)s / {request_id}. A logging.Filter, not a logging.Adapter,
    because this must apply uniformly to every logger call site in the
    codebase (including third-party/Django loggers) without editing any of
    them -- attached once, to the 'console' handler, in
    config.settings.LOGGING."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class UTCFormatter(logging.Formatter):
    """A logging.Formatter whose %(asctime)s is always UTC, regardless of
    the container/host's local timezone -- matching this codebase's
    explicit settings.TIME_ZONE = 'UTC' / USE_TZ = True convention for
    every OTHER timestamp (DB columns, `timezone.now().isoformat()` in
    heartbeat/health payloads). Plain logging.Formatter defaults to
    time.localtime, which isn't governed by Django's TIME_ZONE setting at
    all -- so without this override, log timestamps would silently depend
    on whatever timezone the container happens to boot with, while every
    other timestamp in the system is unambiguously UTC. Standard fix from
    the Python logging cookbook ("Formatting times using UTC (GMT) via
    configuration")."""

    converter = time.gmtime
