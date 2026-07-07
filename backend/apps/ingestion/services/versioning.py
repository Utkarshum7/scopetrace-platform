"""
Phase 6b — EmissionRecordVersion creation logic. Kept out of the model
(apps/ingestion/models.py's EmissionRecord.save() just calls
create_version_if_changed()) matching this project's established pattern
of thin models/views, business logic in services/.
"""
import logging

from apps.ingestion.models import EmissionRecordVersion

logger = logging.getLogger(__name__)

# The business fields that count as a "meaningful modification" — compared
# between the pre-save and post-save state to decide whether a new version
# is warranted. Deliberately excludes id/batch/organization/row_index
# (structural, never legitimately change) and created_at/updated_at (pure
# bookkeeping, not business state).
_COMPARED_FIELDS = (
    "status", "is_suspicious", "scope_category", "normalized_value",
    "normalized_unit", "approved_by_id", "approved_at", "validation_errors",
    "raw_data_payload",
)


def _current_calculation(record):
    # Deferred import — apps.carbon depends on apps.ingestion (its models
    # reference "ingestion.EmissionRecord" as a lazy string), so importing
    # apps.carbon.models at this module's top level risks import-order
    # issues during Django app loading. Matches the deferred-import pattern
    # already established elsewhere in this codebase (e.g.
    # apps.ingestion.tasks's deferred apps.core.tasks import).
    from apps.carbon.models import EmissionCalculation

    return (
        EmissionCalculation.objects.filter(emission_record=record, is_current=True)
        .order_by("-calculated_at")
        .first()
    )


def _fields_changed(old_record, new_record) -> bool:
    if old_record is None:
        return True  # first save — always version the initial state
    for field in _COMPARED_FIELDS:
        if getattr(old_record, field) != getattr(new_record, field):
            return True
    return False


def _next_version_number(record) -> int:
    return EmissionRecordVersion.objects.filter(record=record).count() + 1


def _build_version(*, record, calculation, changed_by, reason) -> EmissionRecordVersion:
    version = EmissionRecordVersion(
        record=record,
        record_uuid_backup=record.id,
        organization=record.organization,
        version_number=_next_version_number(record),
        status=record.status,
        is_suspicious=record.is_suspicious,
        scope_category=record.scope_category,
        normalized_value=record.normalized_value,
        normalized_unit=record.normalized_unit,
        approved_by_id=record.approved_by_id,
        approved_at=record.approved_at,
        validation_errors=record.validation_errors,
        raw_data_payload=record.raw_data_payload,
        calculation=calculation,
        created_by=changed_by,
        reason=reason,
    )
    version.save()
    logger.info(
        "EmissionRecordVersion: record %s -> version %s", record.id, version.version_number
    )
    return version


def create_version_if_changed(*, old_record, new_record, changed_by=None, reason=None):
    """Called from EmissionRecord.save() after the record itself has been
    persisted. Returns the created EmissionRecordVersion, or None if nothing
    business-meaningful changed (the required duplicate-prevention: an
    unchanged record must not accumulate a new version on every save()).

    This is a FIELD-DIFF trigger — it only fires when one of _COMPARED_FIELDS
    actually changed. It deliberately does NOT fire on a recalculation
    (which changes which EmissionCalculation is "current" for the record
    without touching any of the record's own fields) — see
    create_version_for_calculation_change() below for that case.
    """
    if not _fields_changed(old_record, new_record):
        return None
    return _build_version(
        record=new_record,
        calculation=_current_calculation(new_record),
        changed_by=changed_by,
        reason=reason,
    )


def create_version_for_calculation_change(*, record, changed_by=None, reason=None):
    """Called explicitly by the /recalculate/ action — recalculation changes
    WHICH EmissionCalculation is current for a record without changing any
    of the record's own fields, so the generic field-diff trigger above
    would never fire and the version history's calculation reference would
    go stale. This is an intentional, unconditional version (the caller
    already knows a meaningful change happened — a new calculation was just
    computed and marked current — so there's no diff to check here).
    """
    return _build_version(
        record=record,
        calculation=_current_calculation(record),
        changed_by=changed_by,
        reason=reason,
    )


def create_initial_versions_bulk(records):
    """Called once, right after apps.ingestion.services.ingestion_service's
    EmissionRecord.objects.bulk_create(records_to_create) — bulk_create()
    bypasses Model.save() entirely (a Django design constraint, not an
    oversight), so freshly-ingested records need their own explicit "version
    1" creation here. Uses bulk_create itself for the same reason ingestion
    does: avoiding an N+1 query regression in the one genuinely hot path
    this system has.

    No calculation reference is set here — calculate_task runs AFTER
    ingestion completes (the chained second stage), so no EmissionCalculation
    exists yet at this point for any of these records.
    """
    versions = [
        EmissionRecordVersion(
            record=record,
            record_uuid_backup=record.id,
            organization=record.organization,
            version_number=1,
            status=record.status,
            is_suspicious=record.is_suspicious,
            scope_category=record.scope_category,
            normalized_value=record.normalized_value,
            normalized_unit=record.normalized_unit,
            approved_by_id=record.approved_by_id,
            approved_at=record.approved_at,
            validation_errors=record.validation_errors,
            raw_data_payload=record.raw_data_payload,
            calculation=None,
        )
        for record in records
    ]
    EmissionRecordVersion.objects.bulk_create(versions)
    return versions
