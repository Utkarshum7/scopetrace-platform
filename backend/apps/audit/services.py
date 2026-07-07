"""
Audit hash-chain — computation, atomic append, and verification (Phase 6a).

Business logic kept out of apps/audit/models.py, matching this project's
established pattern (thin models, logic in services/) — see e.g.
apps.ingestion.services.ingestion_service, apps.carbon.services.carbon_service.
"""
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from django.utils import timezone

from apps.audit.models import GENESIS_HASH, AuditChainState, AuditTrail

logger = logging.getLogger(__name__)


def _canonical_payload(*, sequence, organization_id, record_uuid_backup, action,
                        changed_by_id, changes, reason, timestamp_iso, prev_hash):
    """The exact fields hashed into entry_hash — order-independent (sort_keys),
    type-stable (UUIDs/None normalized to str/None before hashing) so the same
    logical entry always hashes identically regardless of how it was
    constructed in Python."""
    return {
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


def compute_entry_hash(**payload_kwargs) -> str:
    payload = _canonical_payload(**payload_kwargs)
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_entry(
    *, organization, action, changed_by=None, record=None,
    record_uuid_backup=None, changes=None, reason=None,
) -> AuditTrail:
    """The ONLY sanctioned way to create an AuditTrail row — assigns
    sequence/prev_hash/entry_hash atomically under AuditChainState's lock.

    Must be called from within an existing `transaction.atomic()` block (both
    current call sites — approve, recalculate — already wrap their whole
    state change in one); `select_for_update()` here participates in that
    same transaction rather than opening its own, so the lock is held for the
    same duration as the record-level change it's accompanying.
    """
    state, _ = AuditChainState.objects.select_for_update().get_or_create(organization=organization)

    sequence = state.last_sequence + 1
    timestamp = timezone.now()
    changed_by_id = changed_by.id if changed_by is not None else None
    record_uuid = record_uuid_backup or (record.id if record is not None else None)

    entry_hash = compute_entry_hash(
        sequence=sequence,
        organization_id=organization.id,
        record_uuid_backup=record_uuid,
        action=action,
        changed_by_id=changed_by_id,
        changes=changes,
        reason=reason,
        timestamp_iso=timestamp.isoformat(),
        prev_hash=state.last_hash,
    )

    entry = AuditTrail(
        organization=organization,
        record=record,
        record_uuid_backup=record_uuid,
        action=action,
        changed_by=changed_by,
        changes=changes or {},
        reason=reason,
        timestamp=timestamp,
        sequence=sequence,
        prev_hash=state.last_hash,
        entry_hash=entry_hash,
    )
    # AuditTrail.timestamp is a plain field (deliberately not auto_now_add —
    # see that field's own comment) specifically so the value hashed above
    # and the value actually persisted below are guaranteed identical.
    entry.save()

    state.last_sequence = sequence
    state.last_hash = entry_hash
    state.save(update_fields=["last_sequence", "last_hash", "updated_at"])

    return entry


@dataclass
class ChainVerificationResult:
    organization_id: str
    valid: bool
    entries_checked: int
    broken_at_sequence: Optional[int] = None
    detail: str = "OK"
    errors: list = field(default_factory=list)


def verify_chain(organization) -> ChainVerificationResult:
    """Walks one organization's chain in sequence order, recomputing each
    entry's hash and confirming it matches both the stored entry_hash and the
    next entry's prev_hash. Read-only — safe to call at any time, including
    from an unauthenticated-adjacent context like a scheduled health check
    (not wired up as one yet, see docs/GOVERNANCE.md for why)."""
    entries = list(
        AuditTrail.objects.filter(organization=organization).order_by("sequence")
    )

    expected_prev = GENESIS_HASH
    errors = []
    broken_at_sequence = None
    for entry in entries:
        if entry.prev_hash != expected_prev:
            errors.append(
                f"sequence {entry.sequence}: prev_hash mismatch "
                f"(expected {expected_prev}, got {entry.prev_hash})"
            )
            broken_at_sequence = entry.sequence
            break

        recomputed = compute_entry_hash(
            sequence=entry.sequence,
            organization_id=entry.organization_id,
            record_uuid_backup=entry.record_uuid_backup,
            action=entry.action,
            changed_by_id=entry.changed_by_id,
            changes=entry.changes,
            reason=entry.reason,
            timestamp_iso=entry.timestamp.isoformat(),
            prev_hash=entry.prev_hash,
        )
        if recomputed != entry.entry_hash:
            errors.append(
                f"sequence {entry.sequence}: entry_hash mismatch "
                f"(stored {entry.entry_hash}, recomputed {recomputed}) — this entry's "
                f"content has been altered since it was written"
            )
            broken_at_sequence = entry.sequence
            break

        expected_prev = entry.entry_hash

    if errors:
        # Phase 6f: CRITICAL, not just returned in the result -- a broken
        # hash chain means tampering was detected. Logging it HERE (not at
        # each call site) means every caller gets this for free: the API
        # endpoint (apps.audit.views.AuditChainVerifyView) and the
        # verify_audit_chain management command both already surface the
        # result to their own caller, but neither previously emitted a
        # log line an operator's alerting could actually catch.
        logger.critical(
            "Audit hash chain BROKEN for organization %s at sequence %s: %s",
            organization.id, broken_at_sequence, errors[0],
        )
        return ChainVerificationResult(
            organization_id=str(organization.id),
            valid=False,
            entries_checked=len(entries),
            broken_at_sequence=broken_at_sequence,
            detail=errors[0],
            errors=errors,
        )

    return ChainVerificationResult(
        organization_id=str(organization.id),
        valid=True,
        entries_checked=len(entries),
        detail="Chain intact." if entries else "Chain empty — no audit entries yet.",
    )
