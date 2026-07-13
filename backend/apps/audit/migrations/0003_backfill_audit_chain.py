# Phase 6a — backfills sequence/prev_hash/entry_hash for AuditTrail rows that
# existed before the hash chain was introduced, and creates the
# corresponding AuditChainState "tip" row per organization so real-time
# apps.audit.services.append_entry() calls continue the chain correctly
# from here.
#
# Deliberately duplicates apps.audit.services.compute_entry_hash's logic
# inline rather than importing it — Django's own migration best practice:
# a migration must remain replayable indefinitely regardless of how the real
# app code evolves later, so it operates only on historical models
# (apps.get_model) and self-contained logic, never live application code.
import hashlib
import json

from django.db import migrations

GENESIS_HASH = "0" * 64


def _compute_hash(*, sequence, organization_id, record_uuid_backup, action,
                   changed_by_id, changes, reason, timestamp_iso, prev_hash):
    payload = {
        "sequence": sequence,
        "organization_id": str(organization_id),
        "record_uuid_backup": str(record_uuid_backup) if record_uuid_backup else None,
        "action": action,
        "changed_by_id": changed_by_id,
        "changes": changes or {},
        "reason": reason,
        "timestamp": timestamp_iso,
        "prev_hash": prev_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backfill_chain(apps, schema_editor):
    AuditTrail = apps.get_model("audit", "AuditTrail")
    AuditChainState = apps.get_model("audit", "AuditChainState")
    Organization = apps.get_model("core", "Organization")

    # Historical models from apps.get_model() carry no custom save()/clean()
    # overrides (those live on the real model class, not the migration
    # state) — entry.save() below is plain Model.save(), not the app's
    # append-only-enforcing override, which is exactly what a one-time,
    # order-controlled backfill needs.
    for org in Organization.objects.all():
        # (timestamp, id) is a deterministic, reproducible ordering for this
        # one-time backfill — id is a random UUID, not causally meaningful,
        # but it doesn't need to be: it only needs to break exact-timestamp
        # ties consistently, which it does.
        entries = list(
            AuditTrail.objects.filter(organization=org).order_by("timestamp", "id")
        )
        if not entries:
            continue

        prev_hash = GENESIS_HASH
        for i, entry in enumerate(entries, start=1):
            entry_hash = _compute_hash(
                sequence=i,
                organization_id=org.id,
                record_uuid_backup=entry.record_uuid_backup,
                action=entry.action,
                changed_by_id=entry.changed_by_id,
                changes=entry.changes,
                reason=entry.reason,
                timestamp_iso=entry.timestamp.isoformat(),
                prev_hash=prev_hash,
            )
            entry.sequence = i
            entry.prev_hash = prev_hash
            entry.entry_hash = entry_hash
            entry.save(update_fields=["sequence", "prev_hash", "entry_hash"])
            prev_hash = entry_hash

        AuditChainState.objects.update_or_create(
            organization=org,
            defaults={"last_sequence": len(entries), "last_hash": prev_hash},
        )


def noop_reverse(apps, schema_editor):
    # Not meaningfully reversible — the point is establishing a real chain
    # over pre-existing history. A no-op reverse leaves the (still nullable
    # at this point in the migration sequence) fields populated rather than
    # pretending to "undo" a hash computation.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('audit', '0002_add_hash_chain_fields_nullable'),
    ]

    operations = [
        migrations.RunPython(backfill_chain, noop_reverse),
    ]
