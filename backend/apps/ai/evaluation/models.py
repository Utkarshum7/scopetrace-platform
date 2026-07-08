"""
Phase 7a.5 -- AI Evaluation Infrastructure persistence.

EvaluationRun/EvaluationResult are platform-level engineering artifacts, not
tenant data -- they record "did the platform's own prompt/schema contracts
still hold" (a CI/regression concern), never anything about a specific
organization's usage. Unlike AIInteraction, neither model has an
`organization` FK: evaluation runs test capability CONTRACTS against golden
fixtures, not real tenant calls.
"""
import uuid

from django.db import models


class EvaluationRun(models.Model):
    """One row per evaluation invocation (a CI job run, or a manual
    `manage.py run_ai_evaluation` call). `EvaluationResult` rows (one per
    golden-dataset case) belong to exactly one run, mirroring how a test
    suite run has many individual test results."""

    class Tier(models.TextChoices):
        TIER_1_DETERMINISTIC = "TIER_1_DETERMINISTIC", "Tier 1 (deterministic, blocking)"
        TIER_2_ADVISORY = "TIER_2_ADVISORY", "Tier 2 (LLM-judge/qualitative, advisory)"

    class Status(models.TextChoices):
        RUNNING = "RUNNING", "Running"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed (the run itself errored, not an individual case)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tier = models.CharField(max_length=30, choices=Tier.choices)
    trigger = models.CharField(
        max_length=50, default="manual",
        help_text="Free text: 'ci', 'manual', a CI run id, etc. -- diagnostic context, not a closed enum.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    total_cases = models.PositiveIntegerField(default=0)
    passed_cases = models.PositiveIntegerField(default=0)
    failed_cases = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["tier", "-started_at"])]

    def __str__(self):
        return f"{self.tier} run {self.id} ({self.status}, {self.passed_cases}/{self.total_cases})"


class EvaluationResult(models.Model):
    """One row per golden-dataset case evaluated within a run. `outcome`
    distinguishes the four required failure categories (plus OK) so a CI
    log or a query can immediately tell WHY a case failed, not just THAT it
    did."""

    class Outcome(models.TextChoices):
        OK = "OK", "Ok"
        SCHEMA_INVALID = "SCHEMA_INVALID", "Schema invalid"
        REGRESSION = "REGRESSION", "Regression (prompt/schema drift or score below threshold)"
        PROVIDER_ERROR = "PROVIDER_ERROR", "Provider error"
        EVALUATION_FAILURE = "EVALUATION_FAILURE", "Evaluation failure (harness/scoring error)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(EvaluationRun, on_delete=models.CASCADE, related_name="results")
    capability = models.CharField(max_length=100)
    case_id = models.CharField(max_length=200)
    prompt_name = models.CharField(max_length=100)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    score = models.FloatField(null=True, blank=True)
    detail = models.TextField(blank=True)
    prompt_template_hash = models.CharField(max_length=64, blank=True)
    rendered_input_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["run", "outcome"]),
            models.Index(fields=["capability", "case_id", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.capability}/{self.case_id} [{self.outcome}]"
