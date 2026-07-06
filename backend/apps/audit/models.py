import uuid
from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from apps.core.models import Organization

# Sentinel prev_hash for the first entry in an organization's chain — a real
# constant rather than None, so hash computation and verification never need
# to special-case "no previous entry" separately from "previous entry's hash
# happens to be this value" (astronomically unlikely, SHA-256).
GENESIS_HASH = "0" * 64


class AuditTrailQuerySet(models.QuerySet):
    """Blocks bulk delete/update at the QuerySet level — the gap instance-level
    `delete()`/`clean()` overrides don't cover. `AuditTrail.objects.filter(...)
    .delete()` (or `.update(...)`) bypasses Model.delete()/Model.clean()
    entirely, the exact same Django gotcha already hit twice this session
    (QuerySet.update() bypassing signals in Phase 5f/5g's maintenance sweeps).
    Without this override, "append-only" was only ever true by convention for
    single-instance operations."""

    def delete(self):
        raise ValidationError("Audit logs are append-only and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("Audit logs are append-only and cannot be bulk-updated.")


class AuditTrail(models.Model):
    """
    An immutable, append-only, hash-chained log capturing all state changes,
    analyst reviews, manual corrections, and system status adjustments for
    tracking and regulatory reviews.

    Phase 6a: each entry carries a per-organization `sequence` and an
    `entry_hash` = SHA256(canonical JSON of this entry's fields + the
    previous entry's hash) — see apps/audit/services.py for the actual
    computation and apps/audit/models.py's AuditChainState below for how
    sequence/prev_hash are assigned atomically. This makes tampering with
    historical rows *detectable* (any edit changes that row's hash, breaking
    every subsequent link) — it does not make tampering *impossible* for
    someone with raw DB access and the ability to recompute the chain from
    that point forward; true non-repudiation would need an external anchor,
    deliberately out of scope (see docs/GOVERNANCE.md).

    Entries must be created via apps.audit.services.append_entry(...), never
    AuditTrail.objects.create(...) directly — only append_entry() assigns a
    correct sequence/prev_hash/entry_hash under the AuditChainState lock.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        # Phase 6a: was CASCADE — deleting an Organization would have silently
        # destroyed its entire audit history, exactly backwards for what this
        # model exists to guarantee. PROTECT means an org with any audit
        # history can no longer be deleted at all; a real behavior change,
        # deliberate — see docs/GOVERNANCE.md.
        on_delete=models.PROTECT,
        related_name="audit_trails",
        help_text="Tenant context of this audit log"
    )
    # Reference to original EmissionRecord. We set to SET_NULL so that the log
    # remains if a record is deleted, but we also save the direct record ID backup.
    record = models.ForeignKey(
        "ingestion.EmissionRecord",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        help_text="Associated emission record transaction"
    )
    record_uuid_backup = models.UUIDField(
        null=True,
        blank=True,
        help_text="Immutable copy of the record ID to preserve history if record is deleted"
    )
    action = models.CharField(
        max_length=100,
        help_text="Type of action performed (e.g. RECORD_UPDATE, STATUS_CHANGE, AUDIT_LOCK)"
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="User who executed this action"
    )
    changes = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dictionary containing before/after states of changed fields"
    )
    reason = models.TextField(
        null=True,
        blank=True,
        help_text="Analyst-provided comment explaining this manual edit or status change"
    )
    # Phase 6a: NOT auto_now_add. auto_now_add unconditionally overwrites
    # whatever value is assigned at .save()-time with a fresh timezone.now()
    # call — apps.audit.services.append_entry() needs the EXACT timestamp it
    # hashed to be the one actually persisted (otherwise every entry would
    # fail verification the moment it was written, since the hash would
    # embed a timestamp microseconds earlier than what auto_now_add stored).
    # append_entry() is the only sanctioned creation path and always sets
    # this explicitly, so a plain field (no default) is correct here.
    timestamp = models.DateTimeField()

    # --- Phase 6a: hash chain -------------------------------------------
    # (Added nullable in migration 0002, backfilled in 0003, enforced NOT
    # NULL here / in migration 0004 — this model file reflects the final
    # state, per Django convention; only the migration history is 2-phase.)
    sequence = models.PositiveBigIntegerField(
        help_text="This organization's monotonic position in its audit chain (1-indexed).",
    )
    prev_hash = models.CharField(
        max_length=64,
        help_text="entry_hash of this organization's previous chain entry, or GENESIS_HASH for the first.",
    )
    entry_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text="SHA-256 of this entry's canonical payload (including prev_hash) — the chain link.",
    )

    objects = AuditTrailQuerySet.as_manager()

    class Meta:
        verbose_name = "Audit Trail Log"
        verbose_name_plural = "Audit Trail Logs"
        ordering = ["-timestamp"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "sequence"], name="unique_org_audit_sequence"),
        ]

    def clean(self):
        super().clean()
        if self.pk and AuditTrail.objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit logs are read-only and cannot be altered or modified.")

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit logs are append-only and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.action} on {self.timestamp.strftime('%Y-%m-%d %H:%M')} by {self.changed_by}"


class AuditChainState(models.Model):
    """One row per organization — the current tip of its audit hash chain.
    Locked via select_for_update() inside the same transaction that appends
    a new AuditTrail entry (see apps.audit.services.append_entry), so
    sequence/prev_hash assignment is atomic under concurrent writers without
    scanning AuditTrail itself (which would need its own race-prone
    "last row for this org" query, and does not benefit from an index the
    way a dedicated single-row-per-org lock target does).

    Deliberately NOT part of the append-only guarantee itself — this is
    mutable bookkeeping (the chain's current tip), not a governance record;
    AuditTrail is the append-only ledger, this is just where the "what's
    next" counter lives.
    """
    organization = models.OneToOneField(
        Organization, on_delete=models.PROTECT, related_name="audit_chain_state"
    )
    last_sequence = models.PositiveBigIntegerField(default=0)
    last_hash = models.CharField(max_length=64, default=GENESIS_HASH)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Audit Chain State"
        verbose_name_plural = "Audit Chain States"

    def __str__(self):
        return f"{self.organization.name}: seq={self.last_sequence}"
