# Phase 6b — creates the "version 1" snapshot for every EmissionRecord that
# already existed before EmissionRecordVersion was introduced. Without this,
# pre-existing records would have no version history at all until their next
# edit, which would break "list versions for a record" for any record nobody
# touches again after this migration ships.
#
# Deliberately duplicates apps.ingestion.services.versioning's snapshot logic
# inline rather than importing it — same reasoning as
# apps/audit/migrations/0003_backfill_audit_chain.py: a migration must stay
# replayable indefinitely regardless of how the real app code evolves later,
# so it only touches historical models (apps.get_model), never live app code.
from django.db import migrations


def backfill_versions(apps, schema_editor):
    EmissionRecord = apps.get_model("ingestion", "EmissionRecord")
    EmissionRecordVersion = apps.get_model("ingestion", "EmissionRecordVersion")
    EmissionCalculation = apps.get_model("carbon", "EmissionCalculation")

    # created_at/id gives a deterministic, reproducible order for this
    # one-time backfill. Every pre-existing record gets exactly one version
    # (version_number=1) — there is no history to reconstruct prior to "now",
    # only the current state, so there is nothing to iterate per-record.
    for record in EmissionRecord.objects.all().order_by("created_at", "id"):
        current_calc = (
            EmissionCalculation.objects.filter(
                emission_record_id=record.id, is_current=True
            )
            .order_by("-calculated_at")
            .first()
        )
        EmissionRecordVersion.objects.create(
            record_id=record.id,
            record_uuid_backup=record.id,
            organization_id=record.organization_id,
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
            calculation_id=current_calc.id if current_calc else None,
            reason="Backfilled by migration 0007 — initial version snapshot of a pre-existing record.",
        )


def noop_reverse(apps, schema_editor):
    # Not meaningfully reversible — reversing would mean deleting history,
    # which contradicts the entire point of an immutable version record.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('ingestion', '0006_emission_record_versioning'),
        ('carbon', '0003_backfill_reporting_dimensions'),
    ]

    operations = [
        migrations.RunPython(backfill_versions, noop_reverse),
    ]
