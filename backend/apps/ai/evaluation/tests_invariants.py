"""
Phase 7a.5 -- the FORMAL invariant verification suite for I1-I6
(docs/AI_ARCHITECTURE.md §1). This module is the single, discoverable
place documenting "what proves each invariant holds" -- it does not
duplicate every existing check; where a check already lives in an
existing test module, this module names it in the relevant class's
docstring and adds any check that's still missing (usually: does the
NEW apps.ai.evaluation package itself also uphold the invariant, not just
apps.ai's original 7a code).

Intended as a merge gate for every future AI milestone (7b+): a new
capability's PR should keep every test in this file passing, not just its
own new tests. None of these tests call a real vendor API or cost money --
this entire suite runs offline, in CI, for free.
"""
import ast
from pathlib import Path

from django.test import SimpleTestCase, TestCase, override_settings

from apps.ai.evaluation.models import EvaluationResult, EvaluationRun
from apps.ai.evaluation.service import run_tier1_evaluation
from apps.ai.models import AIInteraction, TenantAIPolicy
from apps.ai.services.gateway import invoke_ai
from apps.core.models import Organization


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module)
    return imports


class InvariantI1AdvisoryOnlyTests(TestCase):
    """I1: no un-validated response is ever usable. Gateway-level proof
    already lives in tests_gateway.InvokeAISchemaValidationTests; this adds
    the evaluation-runner equivalent -- a SCHEMA_INVALID case's `parsed`
    body must never surface as a usable result."""

    def test_schema_invalid_evaluation_result_has_no_usable_score_of_1(self):
        from apps.ai.evaluation.fixtures.loader import EvaluationCase, load_golden_cases_for_capability
        from apps.ai.evaluation.runner import OUTCOME_SCHEMA_INVALID, EvaluationRunner

        real_case = load_golden_cases_for_capability("foundation.selftest")[0]
        broken_case = EvaluationCase(
            case_id=real_case.case_id, capability=real_case.capability,
            prompt_name=real_case.prompt_name, template_vars=real_case.template_vars,
            expected_response={"acknowledged": "not-a-bool"},
            response_schema_id=real_case.response_schema_id,
            response_schema_version=real_case.response_schema_version,
            expected_prompt_template_hash=real_case.expected_prompt_template_hash,
            expected_rendered_input_hash=real_case.expected_rendered_input_hash,
        )
        outcome = EvaluationRunner().run_case(broken_case)
        self.assertEqual(outcome.outcome, OUTCOME_SCHEMA_INVALID)
        self.assertNotEqual(outcome.score, 1.0)


class InvariantI2NoGovernedDataMutationTests(SimpleTestCase):
    """I2: no code path can mutate governed business data. Gateway-level
    proof already lives in tests_gateway.InvokeAINoGovernedDataMutationTests
    (scans gateway.py only). This extends the same AST-import scan to every
    file in apps.ai.evaluation -- the new package introduced this milestone
    must uphold the same absence-of-import guarantee.

    The guard's actual promise is about APPLICATION code paths, not test
    fixtures -- same principle apps.ai.tests_import_guard already
    establishes for vendor SDK imports (its own _ALLOWED_TEST_FILES).
    tests_invariants.py itself legitimately imports apps.ingestion.models
    to construct a real EmissionRecord for
    InvariantI2AnomalyDetectionConcreteProofTests' behavioral (not
    structural) proof below -- exempted here for that reason, not because
    the invariant doesn't apply to it.
    """

    _BANNED_MODULES = {"apps.ingestion.models", "apps.carbon.models"}
    _ALLOWED_TEST_FILES = {"tests_invariants.py"}

    def test_no_evaluation_module_imports_governed_models(self):
        evaluation_root = Path(__file__).resolve().parent
        violations = []
        for path in evaluation_root.rglob("*.py"):
            rel = path.relative_to(evaluation_root).as_posix()
            if "/migrations/" in f"/{rel}" or rel in self._ALLOWED_TEST_FILES:
                continue
            hit = self._BANNED_MODULES.intersection(_module_imports(path))
            if hit:
                violations.append(f"{rel}: imports {sorted(hit)}")
        self.assertEqual(violations, [], "\n".join(violations))

    def test_evaluation_models_have_no_write_relation_to_governed_models(self):
        # EvaluationRun/EvaluationResult have no FK to EmissionRecord or
        # EmissionCalculation -- confirmed structurally via field
        # introspection, not just by absence of import.
        for model in (EvaluationRun, EvaluationResult):
            for f in model._meta.get_fields():
                related_model = getattr(f, "related_model", None)
                if related_model is not None:
                    self.assertNotIn(
                        related_model.__name__, {"EmissionRecord", "EmissionCalculation"},
                        f"{model.__name__}.{f.name} must never reference {related_model.__name__}",
                    )


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvariantI2AnomalyDetectionConcreteProofTests(TestCase):
    """I2, Phase 7b edition: apps.ai's structural "no import of governed
    models" proof (above) no longer applies on its own once a real
    capability legitimately NEEDS to import EmissionRecord to read it (see
    apps.ai.services.anomaly_detection) -- the invariant shifts from "no
    import" to "read-only usage", which needs a behavioral proof instead:
    every field on the record is byte-identical before and after a
    successful generate_anomaly_explanation() / task run. Duplicates none
    of tests_anomaly_detection.py's/tests_anomaly_task.py's own coverage --
    this is the formal, merge-gate-visible version of the same claim."""

    def setUp(self):
        from apps.core.models import DataSource

        self.org = Organization.objects.create(name="Invariant I2 Anomaly Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )

    def test_generate_anomaly_explanation_never_mutates_any_record_field(self):
        from apps.ai.providers.echo import canned
        from apps.ai.services.anomaly_detection import generate_anomaly_explanation
        from apps.ingestion.models import EmissionRecord, UploadBatch

        batch = UploadBatch.objects.create(organization=self.org, data_source=self.ds, file_name="i2.csv")
        canned_response = {
            "explanation": "x", "contributing_factors": [], "confidence": "LOW",
            "suggested_investigation": "x",
        }
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1, raw_data_payload={"a": 1},
            status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
            normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
            validation_errors={"quantity": [canned(canned_response)]},
        )
        before = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}

        annotation = generate_anomaly_explanation(record)
        self.assertIsNotNone(annotation)  # the call actually succeeded -- a meaningful proof, not a vacuous one

        record.refresh_from_db()
        after = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}
        self.assertEqual(before, after)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvariantI2FactorRecommendationConcreteProofTests(TestCase):
    """I2, Phase 7c edition: the formal, merge-gate-visible proof of the
    milestone's own explicit callout -- "Verify AI never changes the
    deterministic factor." apps.ai.services.factor_recommendation legitimately
    NEEDS to import apps.carbon.models (EmissionCalculation, EmissionFactor)
    to read candidates, so -- exactly like anomaly_detection's proof above --
    the invariant shifts from "no import" to "read-only usage": every field
    on BOTH the calculation and the candidate factor it recommended is
    byte-identical before and after a successful recommend_emission_factor()
    call. Duplicates none of tests_factor_recommendation.py's own coverage
    -- this is the formal version of the same claim, run as part of the
    same merge-gate suite every other invariant in this file belongs to.
    """

    def setUp(self):
        from apps.carbon.tests.factories import activity_type, dataset, factor
        from apps.core.models import DataSource

        self.org = Organization.objects.create(name="Invariant I2 Factor Rec Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor = factor(self.dataset, self.activity_type)

    def test_recommend_emission_factor_never_mutates_calculation_or_factor_fields(self):
        from unittest.mock import patch

        from apps.ai.services.factor_recommendation import recommend_emission_factor
        from apps.ai.services.gateway import AIGatewayResult
        from apps.carbon.models import EmissionCalculation
        from apps.ingestion.models import EmissionRecord, UploadBatch

        batch = UploadBatch.objects.create(organization=self.org, data_source=self.ds, file_name="i2_factor.csv")
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1, raw_data_payload={"a": 1},
            status=EmissionRecord.RecordStatus.DRAFT, normalized_value=500,
            normalized_unit="L", scope_category="SCOPE_1",
        )
        calc = EmissionCalculation.objects.create(
            organization=self.org, emission_record=record, is_current=True,
            resolution_status=EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR,
            activity_type=self.activity_type, scope="SCOPE_1",
            activity_quantity=500, activity_unit="L",
        )

        calc_before = {f.name: getattr(calc, f.name) for f in EmissionCalculation._meta.fields}
        factor_before = {f.name: getattr(self.factor, f.name) for f in self.factor._meta.fields}

        interaction = AIInteraction.objects.create(
            organization=self.org, capability="factor_recommendation", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        parsed = {
            "recommended_candidate_label": "candidate_1", "confidence": "HIGH",
            "explanation": "x", "reasoning": "x", "alternative_candidates": [],
        }
        with patch(
            "apps.ai.services.factor_recommendation.invoke_ai",
            return_value=AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed),
        ):
            recommendation = recommend_emission_factor(record)
        self.assertIsNotNone(recommendation)  # the call actually succeeded -- a meaningful proof, not a vacuous one
        self.assertEqual(recommendation.recommended_factor, self.factor)

        calc.refresh_from_db()
        self.factor.refresh_from_db()
        calc_after = {f.name: getattr(calc, f.name) for f in EmissionCalculation._meta.fields}
        factor_after = {f.name: getattr(self.factor, f.name) for f in self.factor._meta.fields}
        self.assertEqual(calc_before, calc_after)
        self.assertEqual(factor_before, factor_after)
        # The deterministic resolution_status this capability targets is
        # explicitly still UNRESOLVED_NO_FACTOR -- the recommendation is
        # advisory output attached alongside it, never a resolution.
        self.assertEqual(calc_after["resolution_status"], EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR)
        self.assertIsNone(calc_after["emission_factor"])


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvariantI2ValidationAssistanceConcreteProofTests(TestCase):
    """I2, Phase 7d edition: the formal, merge-gate-visible proof of this
    milestone's own explicit callout -- "no record mutation, no
    validation status changes, deterministic validator remains
    authoritative." Every field on the record (most importantly `status`
    and `validation_errors`, the deterministic validator's own decision)
    is byte-identical before and after a successful
    generate_validation_assistance() call, and the record is confirmed
    still exactly RecordStatus.FAILED afterward -- the AI's assistance is
    advisory output attached alongside the record, never a re-validation.
    Duplicates none of tests_validation_assistance.py's/
    tests_validation_assistance_task.py's own coverage -- this is the
    formal version of the same claim, run as part of the same merge-gate
    suite every other invariant in this file belongs to."""

    def setUp(self):
        from apps.core.models import DataSource

        self.org = Organization.objects.create(name="Invariant I2 Validation Assist Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )

    def test_generate_validation_assistance_never_mutates_any_record_field(self):
        from apps.ai.providers.echo import canned
        from apps.ai.services.validation_assistance import generate_validation_assistance
        from apps.ingestion.models import EmissionRecord, UploadBatch

        batch = UploadBatch.objects.create(organization=self.org, data_source=self.ds, file_name="i2_validation.csv")
        canned_response = {
            "explanation": "x", "affected_fields": ["quantity"], "confidence": "LOW",
            "suggested_correction": "x",
        }
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1, raw_data_payload={"a": 1},
            status=EmissionRecord.RecordStatus.FAILED,
            validation_errors={"quantity": [canned(canned_response)]},
        )
        before = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}

        annotation = generate_validation_assistance(record)
        self.assertIsNotNone(annotation)  # the call actually succeeded -- a meaningful proof, not a vacuous one

        record.refresh_from_db()
        after = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}
        self.assertEqual(before, after)
        # The deterministic validator's own decision is explicitly still
        # authoritative -- the record remains FAILED, unresolved by AI.
        self.assertEqual(after["status"], EmissionRecord.RecordStatus.FAILED)
        self.assertEqual(after["validation_errors"], before["validation_errors"])


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvariantI2EsgAssistantConcreteProofTests(TestCase):
    """I2, Phase 7e edition: esg_assistant is a genuinely different shape
    from every prior capability -- it has no single governed `record` to
    read, only a retrieval layer (apps.ai.services.esg_context_builder)
    querying several governed models at once. The proof shifts
    accordingly: every governed model this capability's context builder
    reads from (EmissionRecord, EmissionCalculation, EmissionFactor,
    UploadBatch) has an identical row count and identical aggregate
    figures before and after a successful ask_esg_assistant() call --
    proving the capability's OWN write path (AIConversationMessage only)
    never touches any of them, not just that one record's fields didn't
    change. Duplicates none of tests_esg_assistant_service.py's own
    coverage -- this is the formal version of the same claim."""

    def setUp(self):
        from apps.carbon.tests.factories import activity_type, dataset, factor
        from apps.core.models import DataSource

        self.org = Organization.objects.create(name="Invariant I2 ESG Assistant Org")
        TenantAIPolicy.objects.create(organization=self.org, ai_enabled=True, provider_override="echo")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor = factor(self.dataset, self.activity_type)

    def test_ask_esg_assistant_never_mutates_any_governed_model(self):
        from unittest.mock import patch

        from apps.ai.models import AIConversation
        from apps.ai.services.esg_assistant import ask_esg_assistant
        from apps.ai.services.gateway import AIGatewayResult
        from apps.carbon.models import EmissionCalculation, EmissionFactor
        from apps.ingestion.models import EmissionRecord, UploadBatch

        batch = UploadBatch.objects.create(organization=self.org, data_source=self.ds, file_name="i2_esg.csv")
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1, raw_data_payload={"a": 1},
            status=EmissionRecord.RecordStatus.APPROVED, normalized_value=500,
            normalized_unit="L", scope_category="SCOPE_1",
        )
        calc = EmissionCalculation.objects.create(
            organization=self.org, emission_record=record, is_current=True,
            resolution_status=EmissionCalculation.ResolutionStatus.CALCULATED,
            activity_type=self.activity_type, emission_factor=self.factor,
            scope="SCOPE_1", co2e_tonnes="1.500000000", reporting_date="2026-01-15",
        )
        # Refresh before snapshotting -- co2e_tonnes was assigned as a str
        # literal above; a fresh-from-DB read normalizes it to Decimal, the
        # same type refresh_from_db() below will produce for the "after"
        # snapshot. Without this, the two snapshots would differ only by
        # Python type (str vs Decimal) on an unchanged value -- a test bug,
        # not a real mutation.
        record.refresh_from_db()
        calc.refresh_from_db()

        record_before = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}
        calc_before = {f.name: getattr(calc, f.name) for f in EmissionCalculation._meta.fields}
        factor_before = {f.name: getattr(self.factor, f.name) for f in self.factor._meta.fields}
        counts_before = (
            EmissionRecord.objects.count(), EmissionCalculation.objects.count(),
            EmissionFactor.objects.count(), UploadBatch.objects.count(),
        )

        conversation = AIConversation.objects.create(organization=self.org)
        interaction = AIInteraction.objects.create(
            organization=self.org, capability="esg_assistant", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        parsed = {
            "answer": "Total CO2e was 1.5 tonnes.", "citations": ["org_summary"],
            "confidence": "HIGH", "unsupported_claim": False,
        }
        with patch(
            "apps.ai.services.esg_assistant.invoke_ai",
            return_value=AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed),
        ):
            message = ask_esg_assistant(conversation, "What was our total CO2e?")
        self.assertIsNotNone(message)  # the call actually succeeded -- a meaningful proof, not a vacuous one

        record.refresh_from_db()
        calc.refresh_from_db()
        self.factor.refresh_from_db()
        record_after = {f.name: getattr(record, f.name) for f in EmissionRecord._meta.fields}
        calc_after = {f.name: getattr(calc, f.name) for f in EmissionCalculation._meta.fields}
        factor_after = {f.name: getattr(self.factor, f.name) for f in self.factor._meta.fields}
        counts_after = (
            EmissionRecord.objects.count(), EmissionCalculation.objects.count(),
            EmissionFactor.objects.count(), UploadBatch.objects.count(),
        )

        self.assertEqual(record_before, record_after)
        self.assertEqual(calc_before, calc_after)
        self.assertEqual(factor_before, factor_after)
        self.assertEqual(counts_before, counts_after)


class InvariantI3TenantIsolationTests(TestCase):
    """I3: no cross-tenant context ever enters a prompt or a budget total.
    Gateway-level proof already lives in
    tests_gateway.InvokeAITenantIsolationTests. This adds the evaluation
    side: EvaluationRun/EvaluationResult are platform-level (no
    organization FK at all -- verified in tests_models.py), so they
    structurally cannot leak tenant data by construction, and a Tier 1 run
    never touches any organization's AIInteraction history."""

    def test_tier1_run_never_reads_or_writes_ai_interaction(self):
        org = Organization.objects.create(name="Invariant I3 Org")
        TenantAIPolicy.objects.create(organization=org, ai_enabled=True, provider_override="echo")
        invoke_ai(
            organization=org, capability="foundation.selftest", prompt_name="foundation.selftest",
            template_vars={"echo_value": "real tenant call"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        before = AIInteraction.objects.count()

        run_tier1_evaluation(trigger="test")

        self.assertEqual(AIInteraction.objects.count(), before)


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class InvariantI3EsgAssistantConcreteProofTests(TestCase):
    """I3, Phase 7e edition: esg_assistant is the first capability whose
    OWN inputs are built by querying across several governed tables at
    once (apps.ai.services.esg_context_builder), rather than reading one
    already-scoped record -- so tenant isolation needs its own concrete
    proof here, not just a structural absence-of-cross-org-FK argument.
    Also the first capability with a real, dedicated API endpoint, so
    RBAC enforcement (CanUseAI's role gate) gets a merge-gate-visible
    proof of its own too, alongside the general coverage already in
    tests_esg_assistant_api.py.
    """

    def test_ask_esg_assistant_context_never_contains_another_org_s_data(self):
        from unittest.mock import patch

        from apps.ai.models import AIConversation
        from apps.ai.services.esg_assistant import ask_esg_assistant
        from apps.ai.services.gateway import AIGatewayResult
        from apps.core.models import DataSource
        from apps.ingestion.models import UploadBatch

        org_a = Organization.objects.create(name="Invariant I3 ESG Org A")
        org_b = Organization.objects.create(name="Invariant I3 ESG Org B")
        TenantAIPolicy.objects.create(organization=org_a, ai_enabled=True, provider_override="echo")
        ds_b = DataSource.objects.create(
            organization=org_b, name="SAP B", source_type=DataSource.SourceType.SAP_FUEL,
        )
        UploadBatch.objects.create(organization=org_b, data_source=ds_b, file_name="org_b_secret_upload.csv")

        conversation = AIConversation.objects.create(organization=org_a)
        interaction = AIInteraction.objects.create(
            organization=org_a, capability="esg_assistant", provider="echo", model_id="echo-1",
            outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
        )
        parsed = {"answer": "x", "citations": [], "confidence": "LOW", "unsupported_claim": False}
        with patch(
            "apps.ai.services.esg_assistant.invoke_ai",
            return_value=AIGatewayResult(outcome=AIInteraction.Outcome.OK, interaction_id=str(interaction.id), parsed=parsed),
        ):
            message = ask_esg_assistant(conversation, "What datasets have been uploaded?")

        self.assertIsNotNone(message)  # the call actually succeeded -- a meaningful proof, not a vacuous one
        self.assertNotIn("org_b_secret_upload.csv", message.retrieved_context)
        self.assertNotIn("Org B", message.retrieved_context)

    def test_viewer_role_cannot_reach_the_esg_assistant_api(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIClient

        from apps.accounts.models import Membership, Role

        User = get_user_model()
        org = Organization.objects.create(name="Invariant I3 RBAC Org")
        viewer = User.objects.create_user("invariant_i3_viewer", password="pw")
        Membership.objects.create(user=viewer, organization=org, role=Role.VIEWER, active=True)

        client = APIClient()
        client.force_authenticate(viewer)
        response = client.get("/api/esg-assistant/conversations/")
        self.assertEqual(response.status_code, 403)


class InvariantI4ProviderAgnosticTests(SimpleTestCase):
    """I4: application code depends only on LLMProvider/invoke_ai(), never
    a vendor SDK directly. Full proof already lives in
    apps.ai.tests_import_guard (scans the ENTIRE apps/ai tree, including
    apps/ai/evaluation/ -- confirmed by that test's own docstring and
    verified passing after every commit this milestone). This class exists
    so I4 is still named and traceable from this consolidated suite."""

    def test_import_guard_covers_the_evaluation_package(self):
        from apps.ai.tests_import_guard import _BANNED_MODULES

        evaluation_root = Path(__file__).resolve().parent
        violations = []
        for path in evaluation_root.rglob("*.py"):
            hit = _BANNED_MODULES.intersection(_module_imports(path))
            if hit:
                violations.append(f"{path}: imports {sorted(hit)}")
        self.assertEqual(violations, [])


class InvariantI5AuditAndMeteringTests(TestCase):
    """I5: every call is audited/metered. Gateway-level proof already
    lives across tests_gateway.py (every invoke_ai() outcome writes exactly
    one AIInteraction). This adds the evaluation equivalent: every
    evaluation run writes exactly one EvaluationResult per case, with no
    silent gaps."""

    def test_every_case_produces_exactly_one_result_row(self):
        run = run_tier1_evaluation(trigger="test")
        self.assertEqual(EvaluationResult.objects.filter(run=run).count(), run.total_cases)

    def test_run_totals_match_persisted_result_counts(self):
        run = run_tier1_evaluation(trigger="test")
        ok_count = EvaluationResult.objects.filter(run=run, outcome=EvaluationResult.Outcome.OK).count()
        self.assertEqual(run.passed_cases, ok_count)
        self.assertEqual(run.failed_cases, run.total_cases - ok_count)


class InvariantI6FailSafeNotFailOpenTests(TestCase):
    """I6: a failure degrades to a clean, recorded refusal -- never a crash,
    never a silently-accepted invalid result. Gateway-level proof already
    lives in tests_gateway.py (AI_DISABLED/BUDGET_EXCEEDED/EGRESS_BLOCKED
    all return cleanly) and tests_runner.py (every outcome category,
    including EVALUATION_FAILURE, is returned, never raised). This adds
    the Tier 2/judge-specific fail-safe check: the LLM-judge framework must
    be disabled by default, so its mere presence in the codebase can never
    accidentally trigger a real, billable provider call."""

    def test_ai_judge_disabled_by_default(self):
        from django.conf import settings

        self.assertFalse(getattr(settings, "AI_JUDGE_ENABLED", False))

    def test_a_batch_with_one_broken_case_still_completes_the_rest(self):
        from apps.ai.evaluation.fixtures.loader import EvaluationCase, load_golden_cases_for_capability
        from apps.ai.evaluation.runner import OUTCOME_EVALUATION_FAILURE, OUTCOME_OK, EvaluationRunner

        good_case = load_golden_cases_for_capability("foundation.selftest")[0]
        broken_case = EvaluationCase(
            case_id="deliberately-broken", capability="foundation.selftest",
            prompt_name="no.such.template", template_vars={}, expected_response={},
            response_schema_id="foundation.selftest", response_schema_version=1,
            expected_prompt_template_hash="", expected_rendered_input_hash="",
        )
        outcomes = EvaluationRunner().run_cases([broken_case, good_case])
        self.assertEqual(outcomes[0].outcome, OUTCOME_EVALUATION_FAILURE)
        self.assertEqual(outcomes[1].outcome, OUTCOME_OK)
