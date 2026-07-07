"""
Phase 7a — AI Foundation & Governance Seam.

Three models, none of which ever hold governed business data (I1/I2 from
docs/AI_ARCHITECTURE.md's invariants): AIPromptVersion is a registry of what
was asked, AIInteraction is an audit/reproducibility record of what happened
on each call, TenantAIPolicy is per-organization AI configuration. No model
here has a foreign key *from* EmissionRecord/EmissionCalculation, and none of
apps.ai's own logic ever writes to those models -- see
apps/ai/services/gateway.py's docstring for where that boundary is enforced.
"""
import uuid

from django.conf import settings
from django.db import models, transaction

from apps.core.models import Organization


class AIPromptVersion(models.Model):
    """Immutable registry entry for one exact version of one named prompt
    template -- the AI analog of EmissionRecordVersion: "which exact prompt
    produced this AIInteraction" must be answerable forever, even after the
    template file on disk changes.

    Rows are never edited after creation (apps.ai.prompts.registry.render_prompt()
    is the only writer, via get_or_create keyed on (name, template_hash) --
    registering the same template content twice returns the existing row,
    never a duplicate). `version` is assigned once, atomically, the first
    time a given (name, template_hash) pair is seen.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(
        max_length=100,
        help_text="Prompt template name, e.g. 'foundation.selftest' (stable across versions).",
    )
    version = models.PositiveIntegerField(
        help_text="Monotonically increasing per `name`, assigned at first registration of a new template_hash.",
    )
    template_hash = models.CharField(
        max_length=64,
        help_text="SHA-256 hex digest of template_text -- what actually varies per version.",
    )
    template_text = models.TextField(
        help_text="The template itself, verbatim. Not tenant data -- this is our own prompt authoring, safe to store in full for audit/reproducibility.",
    )
    response_schema_id = models.CharField(
        max_length=100,
        help_text="Identifier of the JSON schema this prompt version's response must validate against.",
    )
    response_schema_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["name", "version"], name="ai_promptversion_unique_name_version"),
            models.UniqueConstraint(fields=["name", "template_hash"], name="ai_promptversion_unique_name_hash"),
        ]
        indexes = [models.Index(fields=["name", "-version"])]

    def __str__(self):
        return f"{self.name} v{self.version}"

    @classmethod
    def register(cls, *, name, template_text, template_hash, response_schema_id, response_schema_version):
        """Get-or-create a version row for (name, template_hash). Assigns the
        next version number atomically -- concurrent first-registrations of
        the same brand-new template (from two workers importing the module at
        once) must not both grab the same version number."""
        existing = cls.objects.filter(name=name, template_hash=template_hash).first()
        if existing is not None:
            return existing, False

        with transaction.atomic():
            last = (
                cls.objects.select_for_update()
                .filter(name=name)
                .order_by("-version")
                .first()
            )
            next_version = (last.version + 1) if last else 1
            return cls.objects.create(
                name=name,
                version=next_version,
                template_hash=template_hash,
                template_text=template_text,
                response_schema_id=response_schema_id,
                response_schema_version=response_schema_version,
            ), True


class TenantAIPolicy(models.Model):
    """Per-organization AI configuration. Absence of a row means "AI has
    never been configured for this org" -- apps.ai.services.policy.resolve_policy()
    treats a missing row identically to ai_enabled=False, so a brand-new
    organization is safe (AI off) with zero setup, matching STORAGE_BACKEND's
    fail-closed-by-default philosophy applied per-tenant rather than globally.
    """

    class EgressTier(models.TextChoices):
        REDACTED = "REDACTED", "Redacted (default -- PII/identifier scrubbing before any external call)"
        RAW = "RAW", "Raw (explicit opt-in, no redaction)"
        NO_EGRESS = "NO_EGRESS", "No egress (only zero-egress providers, e.g. echo/self-hosted, permitted)"

    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="ai_policy",
        primary_key=True,
    )
    ai_enabled = models.BooleanField(default=False)
    # '' = use the platform default (settings.AI_PROVIDER / AI_DEFAULT_MODEL).
    provider_override = models.CharField(max_length=50, blank=True)
    model_override = models.CharField(max_length=100, blank=True)
    monthly_budget_usd = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Null = use the platform default budget (settings.AI_DEFAULT_MONTHLY_BUDGET_USD).",
    )
    egress_tier = models.CharField(max_length=20, choices=EgressTier.choices, default=EgressTier.REDACTED)
    byo_api_key_ref = models.CharField(
        max_length=200, blank=True,
        help_text="Reference/secret-name for a tenant-supplied API key (e.g. an env var name or secrets-manager path) -- never the raw key value itself.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AI policy for {self.organization_id} (enabled={self.ai_enabled})"


class AIInteraction(models.Model):
    """One row per call through apps.ai.services.gateway.invoke_ai() --
    including calls that were refused before reaching a provider (disabled,
    over budget, egress-blocked). This is the complete reproducibility +
    audit record: given this row, you can reconstruct exactly what was asked
    (provider/model/prompt_version/parameters/hashes), what tenant data fed
    it (context_provenance), and what happened (outcome/cost/tokens/latency).

    Deliberately does NOT store the raw rendered prompt or raw response text
    -- only hashes -- unless the resolved egress tier explicitly permits it
    (see apps.ai.services.egress). Hashes alone are always enough to prove
    *which* exact input/output pair this is, without holding a second copy
    of potentially-sensitive tenant-derived content at rest.
    """

    class Outcome(models.TextChoices):
        OK = "OK", "Ok"
        DEGRADED = "DEGRADED", "Degraded (succeeded, but with a caveat -- reserved for future use)"
        AI_DISABLED = "AI_DISABLED", "AI disabled (globally or for this tenant)"
        BUDGET_EXCEEDED = "BUDGET_EXCEEDED", "Budget exceeded"
        EGRESS_BLOCKED = "EGRESS_BLOCKED", "Egress policy blocked this call"
        SCHEMA_INVALID = "SCHEMA_INVALID", "Response failed schema validation"
        ERROR = "ERROR", "Provider or gateway error"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        # PROTECT, matching AuditTrail's organization FK: an org with AI
        # history shouldn't silently lose that history via cascade.
        on_delete=models.PROTECT,
        related_name="ai_interactions",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Null for system-initiated calls (e.g. a scheduled task), not just a deleted user.",
    )
    capability = models.CharField(
        max_length=100,
        help_text="Short code for the calling feature, e.g. 'foundation.selftest'. Free text like AuditTrail.action -- new capabilities never require a migration.",
    )

    # --- provider / model -------------------------------------------------
    provider = models.CharField(max_length=50, help_text="Adapter name: 'anthropic' | 'openai' | 'echo'.")
    model_id = models.CharField(max_length=100)
    model_snapshot = models.CharField(max_length=100, blank=True)
    provider_request_id = models.CharField(max_length=200, blank=True)

    # --- prompt -------------------------------------------------------------
    prompt_version = models.ForeignKey(
        AIPromptVersion, on_delete=models.SET_NULL, null=True, blank=True, related_name="interactions",
    )
    # Redundant copy of prompt_version.template_hash at call time -- survives
    # even if the AIPromptVersion row is ever pruned, and avoids a join for
    # verification. Same defense-in-depth reasoning as AuditTrail.record_uuid_backup.
    prompt_template_hash = models.CharField(max_length=64, blank=True)
    rendered_input_hash = models.CharField(max_length=64, blank=True)
    context_provenance = models.JSONField(
        default=list, blank=True,
        help_text="List of record/metric ids that formed the prompt's context -- the retrieval analog of a compliance report's line-item provenance.",
    )
    parameters = models.JSONField(
        default=dict, blank=True,
        help_text="temperature/top_p/max_tokens/seed/stop/response_schema_id/response_schema_version.",
    )

    # --- output -------------------------------------------------------------
    response_hash = models.CharField(max_length=64, blank=True)
    schema_valid = models.BooleanField(null=True, help_text="Null = no schema was expected for this call.")
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    error_detail = models.TextField(blank=True, help_text="Sanitized error/validation detail -- never raw tenant data.")

    # --- economics / performance --------------------------------------------
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)

    # --- governance -----------------------------------------------------------
    egress_tier_applied = models.CharField(max_length=20, choices=TenantAIPolicy.EgressTier.choices)
    redaction_applied = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=100, blank=True, db_index=True)
    gateway_version = models.CharField(max_length=20, default="1")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["organization", "idempotency_key"]),
        ]

    def __str__(self):
        return f"{self.capability} [{self.outcome}] {self.created_at:%Y-%m-%d %H:%M}"
