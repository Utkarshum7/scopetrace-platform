"""
Phase 6d — soft deletion. Orthogonal to the approval workflow (6c):
is_deleted/deleted_at live alongside `status`, never replacing or
transitioning through it -- deleting a record freezes its status exactly
as it was; restoring simply un-hides it at that same status. See
docs/adr/0004-soft-delete-orthogonal-fields.md for the full design,
including why there are no dedicated deleted_by/deletion_reason fields
(AuditTrail + EmissionRecordVersion already capture that provenance).

Mirrors apps.ingestion.services.workflow's lock -> mutate -> save -> audit
structure exactly.
"""
from django.utils import timezone

from apps.audit.services import append_entry


class AlreadyDeletedError(Exception):
    """Raised attempting to soft-delete a record that's already deleted."""


class NotDeletedError(Exception):
    """Raised attempting to restore a record that isn't deleted."""


def soft_delete_record(*, record, actor, reason):
    """`record` must already be fetched with select_for_update() by the
    caller (via EmissionRecord.all_objects, since a not-yet-deleted record
    is still visible through the default manager) inside an open
    transaction.atomic() -- mirrors apps.ingestion.services.workflow.
    transition_record()'s locking contract exactly.

    Allowed regardless of the record's current approval `status`,
    including APPROVED -- EmissionRecord.clean() carves out an explicit
    exception for exactly these two fields so a locked record can still be
    hidden without its certified business state being touched.
    """
    if record.is_deleted:
        raise AlreadyDeletedError("This record has already been deleted.")

    record.is_deleted = True
    record.deleted_at = timezone.now()
    # Phase 6b integration: threaded through as transient instance
    # attributes so EmissionRecord.save() attributes the resulting
    # EmissionRecordVersion to the right user/reason.
    record._version_changed_by = actor
    record._version_reason = reason
    record.save()  # full_clean() (soft-delete carve-out) + 6b snapshot

    changes = {"is_deleted": [False, True]}
    if record._created_version is not None:
        changes["record_version"] = record._created_version.version_number
    append_entry(
        organization=record.organization,
        record=record,
        action="RECORD_SOFT_DELETE",
        changed_by=actor,
        changes=changes,
        reason=reason,
    )
    return record


def restore_record(*, record, actor, reason=""):
    """Same locking contract as soft_delete_record(). Restores the record
    to visibility at whatever `status` it already had -- restoring never
    changes status, only is_deleted/deleted_at, so there's no ambiguity
    about "which prior state" to return to."""
    if not record.is_deleted:
        raise NotDeletedError("This record is not deleted.")

    record.is_deleted = False
    record.deleted_at = None
    record._version_changed_by = actor
    record._version_reason = reason or "Record restored"
    record.save()

    changes = {"is_deleted": [True, False]}
    if record._created_version is not None:
        changes["record_version"] = record._created_version.version_number
    append_entry(
        organization=record.organization,
        record=record,
        action="RECORD_RESTORE",
        changed_by=actor,
        changes=changes,
        reason=reason or "Record restored",
    )
    return record
