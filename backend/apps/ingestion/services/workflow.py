"""
Phase 6c — the fixed (non-configurable) enterprise approval workflow over
EmissionRecord.status:

    DRAFT ──────┐
    SUSPICIOUS ─┼──> SUBMITTED ──> APPROVED  (terminal — audit-locked, see
    VALIDATED ──┘         │                   EmissionRecord.clean())
                          └──> REJECTED ──> SUBMITTED (resubmit after fixing)

FAILED never enters this graph — it's an ingestion-time data-quality
terminal state (validation failed at parse time), corrected by re-uploading,
not by a workflow transition. This matches the pre-6c approve() action's
existing "cannot approve a FAILED record" behavior.

The actual legality of a transition is enforced in EmissionRecord.clean()
itself (EmissionRecord.WORKFLOW_TRANSITIONS) -- not duplicated here -- for
the same reason Phase 6b hooked save() rather than relying on view call
sites alone: a check that only lived in this service would miss Django
Admin edits, direct ORM use, and any future call site. This module owns the
target-status -> (audit action name, default reason, extra field
side-effects) mapping and the lock -> mutate -> save -> audit sequencing,
which is what "approval logic lives in services, not views" means here.

This is intentionally a hardcoded mapping, not a database-configurable
rules engine — the Phase 6 approval (Decision 3) explicitly rejected
building a configurable workflow engine.
"""
from django.utils import timezone

from apps.audit.services import append_entry
from apps.ingestion.models import EmissionRecord

RecordStatus = EmissionRecord.RecordStatus


class InvalidTransitionError(Exception):
    """Raised when the requested action's target status is not a legal
    edge from the record's current status. Callers should translate this
    to a 400 response; EmissionRecord.clean() raises the authoritative
    django.core.exceptions.ValidationError if this check is ever bypassed
    (it isn't, in the sanctioned call path below), so both layers agree."""


_AUDIT_ACTIONS = {
    RecordStatus.SUBMITTED: "RECORD_SUBMISSION",
    RecordStatus.APPROVED: "RECORD_APPROVAL",
    RecordStatus.REJECTED: "RECORD_REJECTION",
}

_DEFAULT_REASONS = {
    RecordStatus.SUBMITTED: "Submitted for approval",
    RecordStatus.APPROVED: "Analyst record approval",
    RecordStatus.REJECTED: "Record rejected",
}


def available_actions(status):
    """The set of target statuses legally reachable from `status` right
    now. Powers both the transition guard in EmissionRecord.clean() and the
    read-only GET /api/records/{id}/workflow/ endpoint."""
    return EmissionRecord.WORKFLOW_TRANSITIONS.get(status, set())


def transition_record(*, record, target_status, actor, reason=""):
    """Apply ONE workflow transition on an already-locked EmissionRecord.

    The caller MUST have fetched `record` via select_for_update() inside an
    open transaction.atomic() -- mirrors the row-locking this project
    already established for AuditTrail (6a) and EmissionRecordVersion (6b)
    concurrency guarantees. Locking the record's own row is sufficient
    here: a transition's legality only depends on that record's own
    current status, and nothing else needs to write to it concurrently for
    this to be race-free (matches 6b's reasoning for why EmissionRecord
    doesn't need a separate counter row the way AuditChainState does).

    Raises InvalidTransitionError early (before ever touching the row) for
    a clear, action-oriented 400 message. If somehow bypassed,
    EmissionRecord.clean() (invoked by record.save() -> full_clean())
    raises django.core.exceptions.ValidationError with an equivalent
    message -- both layers enforce the SAME EmissionRecord.WORKFLOW_
    TRANSITIONS mapping, so they can never disagree.
    """
    valid_targets = available_actions(record.status)
    if target_status not in valid_targets:
        raise InvalidTransitionError(
            f"Cannot transition from {record.status} to {target_status}. "
            f"Valid transitions from {record.status}: "
            f"{sorted(valid_targets) if valid_targets else 'none (terminal state)'}."
        )

    old_status = record.status
    record.status = target_status
    if target_status == RecordStatus.APPROVED:
        record.approved_by = actor
        record.approved_at = timezone.now()

    effective_reason = reason or _DEFAULT_REASONS[target_status]
    # Phase 6b integration: threaded through as transient instance
    # attributes so EmissionRecord.save() attributes the resulting
    # EmissionRecordVersion to the right user/reason (see that method's
    # own comment for why this transient-attribute pattern is used).
    record._version_changed_by = actor
    record._version_reason = effective_reason
    record.save()  # full_clean() (incl. the transition guard) + 6b snapshot

    changes = {"status": [old_status, target_status]}
    if record._created_version is not None:
        changes["record_version"] = record._created_version.version_number
    append_entry(
        organization=record.organization,
        record=record,
        action=_AUDIT_ACTIONS[target_status],
        changed_by=actor,
        changes=changes,
        reason=effective_reason,
    )
    return record
