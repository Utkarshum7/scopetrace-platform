"""
Phase 7a — AI Foundation & Governance Seam. Phase 7b adds AIAnnotation.
Phase 7c adds AIFactorRecommendation. Phase 7d adds a second AIAnnotation
capability (VALIDATION_ASSISTANCE) rather than a third model -- see ADR
0011: every output validation_assistance needs (explanation, affected
fields, confidence, suggested correction) already maps onto AIAnnotation's
existing four columns with no type mismatch, unlike factor_recommendation
which needed a structurally new field (an FK to EmissionFactor). Phase 7e
adds AIConversation + AIConversationMessage -- a genuinely new shape
(multi-turn, user-initiated) that doesn't fit either existing model: it
has no single governed `record` it's attached to, and its rows accumulate
over a conversation rather than being one advisory output per event. Only
AIConversationMessage is immutable (see ADR 0012); AIConversation itself
is a plain, un-guarded container so its `user` FK can safely stay
on_delete=SET_NULL like AIInteraction.actor, without reintroducing the
SET_NULL-cascade-vs-blocked-update bug class ADR 0009 already worked
around for AIAnnotation. Phase 7f adds AIReportNarration -- immutable,
PROTECT-only FKs like AIFactorRecommendation, but keyed by a
(date_from, date_to, scope) report period rather than a single
EmissionRecord, since compliance reports are on-demand query results
(ADR 0002), not persisted rows this model could otherwise reference.

None of these models ever hold or mutate governed business data (I1/I2 from
docs/AI_ARCHITECTURE.md's invariants): AIPromptVersion is a registry of what
was asked, AIInteraction is an audit/reproducibility record of what happened
on each call, TenantAIPolicy is per-organization AI configuration,
AIAnnotation/AIFactorRecommendation/AIConversationMessage are immutable
advisory output attached to a record (or, for AIConversationMessage, to a
conversation). The DIRECTION of reference matters: no governed model
(EmissionRecord, EmissionCalculation, EmissionFactor) has a foreign key TO
anything here, and none of apps.ai's own logic ever writes to a governed
model -- see apps/ai/services/gateway.py's docstring for where that
boundary is enforced. AIAnnotation.record,
AIFactorRecommendation.record/.recommended_factor, and
AIConversationMessage's retrieval context (built read-only, never a FK)
are how the OTHER direction (an apps.ai model referencing governed models,
read-only) stays true -- exactly the "AI reads context, AI never writes
back" shape ADR 0006 requires; nothing in apps.ingestion or apps.carbon
ever imports or writes any model in this file.
"""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from apps.core.models import Organization
from apps.core.querysets import SetNullCascadeSafeQuerySet


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


class AIInteractionQuerySet(SetNullCascadeSafeQuerySet):
    """Phase 7.5 (H4): AIInteraction is apps.ai's single audit/reproducibility
    record -- the one every observability/cost-governance/budget query in
    this codebase reads from -- yet unlike every sibling AI model
    (AIAnnotation, AIFactorRecommendation, AIConversationMessage,
    AIReportNarration), it had no QuerySet-level guard against bulk
    update()/delete(). This was not an oversight: `actor` is on_delete=
    SET_NULL (see this module's own docstring, "AIConversation... can
    safely stay on_delete=SET_NULL like AIInteraction.actor, without
    reintroducing the SET_NULL-cascade-vs-blocked-update bug class ADR
    0009 already worked around for AIAnnotation") -- copying AIAnnotation's
    unconditional update() block here would have broken User.delete() for
    any user who ever triggered an AI call, the exact bug apps.core.
    querysets.SetNullCascadeSafeQuerySet was built to fix elsewhere. That
    reusable base (whitelisting exactly the SET_NULL cascade shape) is the
    correct fix now that it exists.
    """

    update_blocked_message = "AI interactions are immutable and cannot be bulk-updated."

    def delete(self):
        raise ValidationError("AI interactions are immutable and cannot be bulk-deleted.")


class AIInteraction(models.Model):
    """One row per call through apps.ai.services.gateway.invoke_ai() --
    including calls that were refused before reaching a provider (disabled,
    over budget, egress-blocked). This is the complete reproducibility +
    audit record: given this row, you can reconstruct exactly what was asked
    (provider/model/prompt_version/parameters/hashes), what tenant data fed
    it (context_provenance), and what happened (outcome/cost/tokens/latency).

    Deliberately does NOT store the raw rendered prompt -- only hashes.
    Hashes alone are enough to prove *which* exact input this is without
    holding a second copy of potentially-sensitive tenant-derived content at
    rest. The one exception is `response_text`: the raw response is persisted
    ONLY for calls carrying an idempotency_key, so a redelivery can replay the
    identical result rather than losing it (Phase 7.5 H2, Finding 3). Every
    non-idempotent call remains hashes-only.
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
    # Phase 7.5 (H2, Finding 3): the raw provider response, persisted ONLY for
    # calls that carry an idempotency_key, so a redelivered/duplicate call can
    # replay the IDENTICAL parsed result instead of losing it. Without this, a
    # worker crash between the gateway's OK write and a capability service's
    # own persistence (e.g. AIAnnotation) permanently lost the AI output: the
    # replay short-circuit returned parsed=None forever. Non-idempotent calls
    # (idempotency_key="") stay hashes-only, preserving the class-docstring
    # privacy contract for every call that didn't opt into durable replay.
    response_text = models.TextField(blank=True)
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

    objects = AIInteractionQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
            models.Index(fields=["organization", "idempotency_key"]),
        ]
        constraints = [
            # Phase 7.5 (H2, Finding 1): at most ONE successful interaction per
            # (organization, idempotency_key). The DB-level backstop that makes
            # a duplicate OK row structurally impossible even if application
            # serialization is ever bypassed. Partial: only OK rows with a
            # non-empty key are constrained -- failed attempts may repeat (a
            # retry after an ERROR must be allowed), and non-idempotent calls
            # (key="") are unconstrained by design.
            models.UniqueConstraint(
                fields=["organization", "idempotency_key"],
                condition=Q(outcome="OK") & ~Q(idempotency_key=""),
                name="uniq_ai_interaction_ok_per_idempotency_key",
            ),
        ]

    def __str__(self):
        return f"{self.capability} [{self.outcome}] {self.created_at:%Y-%m-%d %H:%M}"


class _AIAnnotationQuerySet(models.QuerySet):
    """Append-only, mirroring AuditTrailQuerySet's exact pattern (see
    apps.audit.models) -- blocks bulk delete()/update() at the QuerySet
    level, the gap instance-level delete()/clean() overrides don't cover.

    Deliberately has NO carve-out for a SET_NULL cascade shape (unlike a
    fix elsewhere in this codebase for a queryset that needed one) --
    AIAnnotation has no nullable FK to anything, by design (record/
    organization/interaction below are all on_delete=PROTECT), so Django's
    deletion Collector can never issue the kind of bulk .update() call
    that class of bug depends on. Blocking update() unconditionally is
    therefore safe here, not just convenient.
    """

    def delete(self):
        raise ValidationError("AI annotations are immutable and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("AI annotations are immutable and cannot be bulk-updated.")


class AIAnnotation(models.Model):
    """Immutable, advisory-only AI output attached to a governed record --
    Phase 7b's first real capability (anomaly_detection) writes these.
    Never a target of any write from human review actions: submit/approve/
    reject (Phase 6c) remain entirely on EmissionRecord's own workflow,
    untouched by anything here. Multiple annotations can accumulate per
    (record, capability) over time -- each one immutable once created; the
    "current" one is simply the latest by created_at. Re-running
    explanation generation (e.g. after a redelivered Celery task) is made
    idempotent at the SERVICE layer (skip if one already exists), not by a
    uniqueness constraint here, so a genuine re-explanation later (a future
    milestone re-running analysis after new context appears) isn't
    foreclosed by a DB constraint that would need migrating away.

    record/organization/interaction are all on_delete=PROTECT -- no
    SET_NULL anywhere on this model, deliberately (see
    _AIAnnotationQuerySet's own docstring for why that matters).
    """

    class Capability(models.TextChoices):
        ANOMALY_DETECTION = "ANOMALY_DETECTION", "Anomaly Detection"
        # Phase 7d: contributing_factors holds affected FIELD NAMES (not
        # qualitative reasons) and suggested_investigation holds the
        # suggested CORRECTION (not an investigation prompt) for this
        # capability's rows -- same columns, capability-specific meaning,
        # exactly like explanation's "why" framing already generalizes.
        # See ADR 0011.
        VALIDATION_ASSISTANCE = "VALIDATION_ASSISTANCE", "Validation Assistance"

    class Confidence(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ai_annotations",
    )
    record = models.ForeignKey(
        "ingestion.EmissionRecord", on_delete=models.PROTECT, related_name="ai_annotations",
    )
    interaction = models.ForeignKey(
        AIInteraction, on_delete=models.PROTECT, related_name="annotations",
        help_text="The exact gateway call that produced this annotation -- full reproducibility metadata lives there.",
    )
    capability = models.CharField(max_length=50, choices=Capability.choices)
    explanation = models.TextField(help_text="Why the record is unusual.")
    contributing_factors = models.JSONField(
        default=list, blank=True, help_text="List of likely contributing factors.",
    )
    confidence = models.CharField(max_length=10, choices=Confidence.choices)
    suggested_investigation = models.TextField(help_text="What an analyst should look into next.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = _AIAnnotationQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["record", "capability", "-created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.pk and AIAnnotation.objects.filter(pk=self.pk).exists():
            raise ValidationError("AI annotations are immutable and cannot be modified after creation.")

    def delete(self, *args, **kwargs):
        raise ValidationError("AI annotations are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.capability} annotation for record {self.record_id}"


class _AIFactorRecommendationQuerySet(models.QuerySet):
    """Append-only, identical reasoning to _AIAnnotationQuerySet -- no
    nullable FK on this model either (recommended_factor is nullable at
    the DB level, but its on_delete is PROTECT, never SET_NULL, so no
    cascade shape can ever require a carve-out here)."""

    def delete(self):
        raise ValidationError("AI factor recommendations are immutable and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("AI factor recommendations are immutable and cannot be bulk-updated.")


class AIFactorRecommendation(models.Model):
    """Immutable, advisory-only AI output recommending an emission factor
    for a record whose deterministic resolution could not confidently
    choose one (EmissionCalculation.resolution_status ==
    UNRESOLVED_NO_FACTOR -- see apps.ai.services.factor_recommendation).

    Never mutates EmissionCalculation or EmissionFactor -- accepting a
    recommendation is, and remains, a human action through the EXISTING
    Org-Admin activity-mapping-and-recalculate flow, untouched by anything
    here.

    recommended_factor is nullable: the AI is explicitly allowed to
    recommend NONE of the candidate factors it was shown (a valid, honest
    outcome distinct from a low-confidence pick) -- and even when it does
    pick one, that FK is populated by the SERVICE resolving the AI's
    chosen candidate LABEL back to a real object already in memory, never
    by trusting an AI-produced identifier directly (LLMs are unreliable at
    reproducing UUIDs verbatim; asking for a label among a small,
    service-provided candidate set avoids that failure mode entirely).

    record/organization/interaction/recommended_factor are all
    on_delete=PROTECT -- no SET_NULL anywhere, same reasoning as
    AIAnnotation.
    """

    class Confidence(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ai_factor_recommendations",
    )
    record = models.ForeignKey(
        "ingestion.EmissionRecord", on_delete=models.PROTECT, related_name="ai_factor_recommendations",
    )
    interaction = models.ForeignKey(
        AIInteraction, on_delete=models.PROTECT, related_name="factor_recommendations",
        help_text="The exact gateway call that produced this recommendation.",
    )
    recommended_factor = models.ForeignKey(
        "carbon.EmissionFactor", null=True, blank=True, on_delete=models.PROTECT,
        related_name="ai_recommendations",
        help_text="Null if the AI recommended none of the candidates it was shown.",
    )
    confidence = models.CharField(max_length=10, choices=Confidence.choices)
    explanation = models.TextField(help_text="Why this candidate (or none) fits.")
    reasoning = models.TextField(help_text="Deterministic factors the AI weighed (region, date, publisher, ...).")
    alternative_candidates = models.JSONField(
        default=list, blank=True, help_text="Other candidate labels the AI considered but ranked lower.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = _AIFactorRecommendationQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["record", "-created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]

    def clean(self):
        super().clean()
        if self.pk and AIFactorRecommendation.objects.filter(pk=self.pk).exists():
            raise ValidationError("AI factor recommendations are immutable and cannot be modified after creation.")

    def delete(self, *args, **kwargs):
        raise ValidationError("AI factor recommendations are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Factor recommendation for record {self.record_id}"


class AIConversation(models.Model):
    """Phase 7e -- a container for one esg_assistant conversation's
    messages. Deliberately NOT immutable/guarded the way AIConversationMessage
    is: a conversation is just a grouping row, not itself advisory output,
    so there's nothing to protect by blocking bulk update/delete on it. That
    absence of a guard is what makes `user` safe as on_delete=SET_NULL
    (matching AIInteraction.actor's own nullable pattern) -- deleting a user
    never needs to cascade into a blocked queryset here. See ADR 0012.

    Org-scoped, not per-user-private: any org member who can use AI can see
    any conversation in their org, matching how AIAnnotation/
    AIFactorRecommendation are already visible org-wide regardless of which
    analyst's action originally triggered them.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ai_conversations",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_conversations",
        help_text="Who started this conversation. Null if that user account was later deleted.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["organization", "-created_at"]),
        ]

    def __str__(self):
        return f"ESG Assistant conversation {self.id}"


class _AIConversationMessageQuerySet(models.QuerySet):
    """Append-only, identical reasoning to _AIAnnotationQuerySet -- no
    SET_NULL field on this model either (conversation/organization/
    interaction are all PROTECT), so no cascade shape can ever require a
    carve-out here."""

    def delete(self):
        raise ValidationError("AI conversation messages are immutable and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("AI conversation messages are immutable and cannot be bulk-updated.")


class AIConversationMessage(models.Model):
    """Immutable, advisory-only turn in an esg_assistant conversation.
    A USER-role message records the human's question (no `interaction` --
    nothing was invoked); an ASSISTANT-role message records the AI's
    answer, always with `interaction` set to the exact gateway call that
    produced it.

    `retrieved_context` persists exactly what apps.ai.services.
    esg_context_builder assembled for THIS turn -- the milestone's
    "retrieved context" display requirement, and a reproducibility record
    matching AIInteraction's own "what was actually sent" philosophy.
    Blank for USER messages (nothing was retrieved to answer a question
    that hasn't been asked yet).

    conversation/organization/interaction are all on_delete=PROTECT -- no
    SET_NULL anywhere, same reasoning as AIAnnotation. `interaction` is
    nullable (blank for USER messages only).
    """

    class Role(models.TextChoices):
        USER = "USER", "User"
        ASSISTANT = "ASSISTANT", "Assistant"

    class Confidence(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ai_conversation_messages",
    )
    conversation = models.ForeignKey(
        AIConversation, on_delete=models.PROTECT, related_name="messages",
    )
    interaction = models.ForeignKey(
        AIInteraction, null=True, blank=True, on_delete=models.PROTECT, related_name="conversation_messages",
        help_text="The exact gateway call that produced this message. Null for USER-role messages.",
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField(help_text="The question (USER) or answer (ASSISTANT).")
    citations = models.JSONField(
        default=list, blank=True, help_text="References into the retrieved context supporting the answer.",
    )
    confidence = models.CharField(max_length=10, choices=Confidence.choices, blank=True)
    unsupported_claim = models.BooleanField(
        default=False, help_text="True if the AI flagged its own answer as not fully supported by the context.",
    )
    retrieved_context = models.TextField(
        blank=True, help_text="The context block retrieved and shown to the AI for this turn.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = _AIConversationMessageQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["organization", "-created_at"]),
        ]
        ordering = ["created_at"]

    def clean(self):
        super().clean()
        if self.pk and AIConversationMessage.objects.filter(pk=self.pk).exists():
            raise ValidationError("AI conversation messages are immutable and cannot be modified after creation.")

    def delete(self, *args, **kwargs):
        raise ValidationError("AI conversation messages are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.role} message in conversation {self.conversation_id}"


class _AIReportNarrationQuerySet(models.QuerySet):
    """Append-only, identical reasoning to _AIFactorRecommendationQuerySet
    -- no SET_NULL field on this model either (organization/interaction
    are both PROTECT), so no cascade shape can ever require a carve-out
    here."""

    def delete(self):
        raise ValidationError("AI report narrations are immutable and cannot be bulk-deleted.")

    def update(self, **kwargs):
        raise ValidationError("AI report narrations are immutable and cannot be bulk-updated.")


class AIReportNarration(models.Model):
    """Immutable, advisory-only AI narrative for one compliance report
    period (organization, date_from, date_to, scope -- the same
    parameters `apps.carbon.services.reports.compliance_summary` takes).
    Compliance reports themselves are on-demand query results, never
    persisted (ADR 0002) -- this model persists only the AI's OWN
    narrative output about a given period, not a snapshot of the
    underlying report data, exactly mirroring how AIAnnotation/
    AIFactorRecommendation persist advisory output about live governed
    data without snapshotting that data itself.

    Built ONLY from approved, deterministic data (apps.ai.services.
    report_context_builder reuses compliance_summary()'s exact APPROVED-
    only filter shape, never apps.carbon.services.metrics.MetricsService,
    which intentionally includes non-approved records for its dashboard
    use case) -- see ADR 0013.

    Four distinct sections, not one narrative blob (unlike the Phase 7a.5
    placeholder v1 schema): executive_summary, key_highlights,
    trend_explanations, recommendations -- matching the milestone's exact
    display requirements.

    organization/interaction are both on_delete=PROTECT -- no SET_NULL
    anywhere, same reasoning as AIAnnotation/AIFactorRecommendation.
    """

    class Confidence(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.PROTECT, related_name="ai_report_narrations",
    )
    interaction = models.ForeignKey(
        AIInteraction, on_delete=models.PROTECT, related_name="report_narrations",
        help_text="The exact gateway call that produced this narration.",
    )
    date_from = models.DateField()
    date_to = models.DateField()
    scope = models.CharField(
        max_length=20, blank=True, help_text="Empty means all scopes -- matches compliance_summary's own scope=None.",
    )
    executive_summary = models.TextField()
    key_highlights = models.JSONField(default=list, blank=True)
    trend_explanations = models.TextField()
    recommendations = models.JSONField(
        default=list, blank=True, help_text="Advisory suggestions only -- never applied automatically.",
    )
    confidence = models.CharField(max_length=10, choices=Confidence.choices)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = _AIReportNarrationQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["organization", "date_from", "date_to", "-created_at"]),
        ]
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if self.pk and AIReportNarration.objects.filter(pk=self.pk).exists():
            raise ValidationError("AI report narrations are immutable and cannot be modified after creation.")

    def delete(self, *args, **kwargs):
        raise ValidationError("AI report narrations are immutable and cannot be deleted.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Report narration {self.date_from}..{self.date_to} ({self.scope or 'ALL'})"
