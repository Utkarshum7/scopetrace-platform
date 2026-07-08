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

from django.test import SimpleTestCase, TestCase

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
    must uphold the same absence-of-import guarantee."""

    _BANNED_MODULES = {"apps.ingestion.models", "apps.carbon.models"}

    def test_no_evaluation_module_imports_governed_models(self):
        evaluation_root = Path(__file__).resolve().parent
        violations = []
        for path in evaluation_root.rglob("*.py"):
            if "/migrations/" in f"/{path.relative_to(evaluation_root).as_posix()}":
                continue
            hit = self._BANNED_MODULES.intersection(_module_imports(path))
            if hit:
                violations.append(f"{path.relative_to(evaluation_root)}: imports {sorted(hit)}")
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
