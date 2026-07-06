"""
Email notifications (Phase 5g) — a thin domain-specific wrapper over
Django's own mail-backend system, not a custom ABC/provider hierarchy.

Django's EMAIL_BACKEND setting already solves "no ESP lock-in": console (dev),
SMTP, and any third-party ESP backend (SendGrid/SES/Mailgun all publish a
compatible EmailBackend) share one interface, swappable via config with zero
call-site changes. Unlike StorageService, there's no gap here to fill — this
module only adds the pieces Django's mail system doesn't have an opinion on:
who the recipient is, what a batch's outcome should say, and when it's
actually appropriate to send anything at all. See docs/NOTIFICATIONS.md for
the full design and the fire-and-forget dispatch model
(apps.core.tasks.send_notification_task).
"""
import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notify_batch_result(batch) -> bool:
    """Send the one, final, consolidated outcome email for a batch, if it's
    actually in a final resting state and has a recipient to send to.

    Returns True if an email was sent, False if skipped (no recipient, or
    the batch isn't in a state this function recognizes as "final" — see
    _compose_message). Never raises for "nothing to do"; a genuine send
    failure (SMTP down) propagates so the calling task's retry policy can
    handle it — this function does not swallow delivery errors.
    """
    recipient = getattr(batch.uploaded_by, "email", None)
    if not recipient:
        logger.info(
            "notify_batch_result: batch %s has no recipient email — skipping", batch.id
        )
        return False

    subject, body = _compose_message(batch)
    if subject is None:
        logger.info(
            "notify_batch_result: batch %s is not in a final notifiable state "
            "(status=%s, calculation_status=%s) — skipping",
            batch.id, batch.status, batch.calculation_status,
        )
        return False

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[recipient],
        fail_silently=False,
    )
    logger.info(
        "notify_batch_result: sent '%s' to %s for batch %s", subject, recipient, batch.id
    )
    return True


def _compose_message(batch):
    """Returns (subject, body), or (None, None) if `batch` isn't currently in
    one of the three final resting states this module knows how to describe:

      1. Ingestion crashed (status=FAILED) — the chain never reached
         calculation at all; this IS the final state.
      2. Ingestion succeeded (COMPLETED/PARTIALLY_COMPLETED) and calculation
         also succeeded (CALCULATED) — the whole pipeline is done.
      3. Ingestion succeeded but calculation failed
         (calculation_status=CALCULATION_FAILED).

    Deliberately only one email per batch, not one per stage — a batch whose
    ingestion succeeded doesn't get a separate "ingestion succeeded" email,
    since the user cares about the pipeline's actual outcome, not its
    intermediate stages, and a mid-pipeline email would be premature (the
    calculation stage hasn't run yet, so it isn't yet the "final" outcome).
    """
    from apps.ingestion.models import UploadBatch

    if batch.status == UploadBatch.BatchStatus.FAILED:
        subject = f"[ScopeTrace] Upload failed: {batch.file_name}"
        body = (
            f"Your upload of '{batch.file_name}' failed during ingestion and could "
            f"not be processed.\n\n"
            f"Reason: {batch.error_message or 'Unknown error'}\n\n"
            f"Batch ID: {batch.id}\n"
        )
        return subject, body

    if batch.status in (
        UploadBatch.BatchStatus.COMPLETED,
        UploadBatch.BatchStatus.PARTIALLY_COMPLETED,
    ):
        if batch.calculation_status == UploadBatch.CalculationStatus.CALCULATED:
            subject = f"[ScopeTrace] Upload processed: {batch.file_name}"
            body = (
                f"Your upload of '{batch.file_name}' has been fully processed.\n\n"
                f"Status: {batch.get_status_display()}\n"
                f"Rows processed: {batch.total_rows}\n"
                f"Rows failed validation: {batch.failed_rows}\n\n"
                f"Batch ID: {batch.id}\n"
            )
            return subject, body

        if batch.calculation_status == UploadBatch.CalculationStatus.CALCULATION_FAILED:
            subject = f"[ScopeTrace] Upload calculation failed: {batch.file_name}"
            body = (
                f"Your upload '{batch.file_name}' was ingested successfully, but "
                f"carbon calculation failed.\n\n"
                f"Reason: {batch.error_message or 'Unknown error'}\n\n"
                f"Rows processed: {batch.total_rows}\n"
                f"Batch ID: {batch.id}\n"
            )
            return subject, body

    return None, None
