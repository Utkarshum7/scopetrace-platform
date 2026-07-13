"""
Phase 7.5 (H4-13) -- operator-facing DLQ replay.

FailedTaskLog (apps.tasks.models) has been write-only since Phase 5e: a
permanently-failed task is durably logged (task_name/args/kwargs/exception),
but there was no way to actually DO anything with that record short of a
hand-written Django shell command. The audit flagged this as "becomes High
once failure volume is non-trivial" -- an operator with a real backlog of
dead-lettered tasks (e.g. after a prolonged DB/broker outage) had no
supported recovery path.

Deliberately NOT a new DLQ mechanism, no new model, no automatic retry
policy change -- this re-uses FailedTaskLog exactly as it already exists and
re-dispatches the ORIGINAL task by its registered name via Celery's own
app.send_task(), so it goes through the exact same queue routing
(CELERY_TASK_ROUTES) and the target task's own existing idempotency guard
(ingest_task/calculate_task already tolerate redelivery by design -- see
their own docstrings) as a normal at-least-once redelivery would. This is an
operator action, never automatic -- a task landed in the DLQ because
something judged its retries exhausted; blindly auto-replaying could loop
forever on a truly broken input, so replay is opt-in and one row at a time
(or an explicit filtered batch), never a scheduled sweep.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.tasks.models import FailedTaskLog


class Command(BaseCommand):
    help = (
        "Re-dispatch a dead-lettered task from FailedTaskLog by re-sending its "
        "original name/args/kwargs through Celery. Read-only by default (use "
        "--replay to actually dispatch)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--id", dest="log_id", default=None,
            help="Replay a single FailedTaskLog row by its id.",
        )
        parser.add_argument(
            "--task-name", dest="task_name", default=None,
            help="Replay every FailedTaskLog row matching this exact task_name "
                 "(e.g. apps.ingestion.tasks.ingest_task).",
        )
        parser.add_argument(
            "--replay", action="store_true",
            help="Actually dispatch. Without this flag, only lists what WOULD "
                 "be replayed (the safe default).",
        )
        parser.add_argument(
            "--delete-on-replay", action="store_true",
            help="Delete each FailedTaskLog row after successfully dispatching "
                 "its replay, so it doesn't show up as still-dead-lettered. Off "
                 "by default -- the log row is harmless to leave in place (it's "
                 "an observability record, not task state, see FailedTaskLog's "
                 "own docstring), and keeping it preserves the failure history.",
        )

    def handle(self, *args, **options):
        log_id = options["log_id"]
        task_name = options["task_name"]
        if not log_id and not task_name:
            raise CommandError("Provide either --id or --task-name.")
        if log_id and task_name:
            raise CommandError("Provide --id or --task-name, not both.")

        qs = FailedTaskLog.objects.all()
        if log_id:
            qs = qs.filter(id=log_id)
        else:
            qs = qs.filter(task_name=task_name)
        logs = list(qs.order_by("created_at"))

        if not logs:
            self.stdout.write(self.style.WARNING("No matching FailedTaskLog rows."))
            return

        if not options["replay"]:
            self.stdout.write(f"Would replay {len(logs)} task(s) (dry run -- pass --replay to dispatch):")
            for log in logs:
                self.stdout.write(f"  {log.id}  {log.task_name}  args={log.args} kwargs={log.kwargs}")
            return

        # Imported lazily so `manage.py help` never needs a broker connection.
        from config.celery import app as celery_app

        replayed = 0
        for log in logs:
            celery_app.send_task(log.task_name, args=log.args, kwargs=log.kwargs)
            replayed += 1
            self.stdout.write(f"Replayed {log.task_name} (was FailedTaskLog {log.id})")
            if options["delete_on_replay"]:
                log.delete()

        self.stdout.write(self.style.SUCCESS(f"Replayed {replayed} task(s)."))
