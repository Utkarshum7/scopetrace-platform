"""Phase 7a.5 -- capability registry + golden-dataset fixture loader tests."""
import json

from django.test import SimpleTestCase

from django.test import TestCase

from apps.ai.evaluation.capabilities import CAPABILITY_REGISTRY, get_capability_config
from apps.ai.evaluation.fixtures.loader import (
    load_all_golden_cases,
    load_golden_cases,
    load_golden_cases_for_capability,
)
from apps.ai.prompts.registry import render_prompt
from apps.ai.schemas import get_schema, validate_response

ALL_CAPABILITIES = [
    "foundation.selftest", "anomaly_detection", "factor_recommendation",
    "validation_assistance", "esg_assistant", "report_narration",
]


class CapabilityRegistryTests(SimpleTestCase):
    def test_all_six_capabilities_registered(self):
        self.assertEqual(set(CAPABILITY_REGISTRY.keys()), set(ALL_CAPABILITIES))

    def test_unknown_capability_raises_key_error(self):
        with self.assertRaises(KeyError):
            get_capability_config("no_such_capability")

    def test_every_registered_schema_id_resolves_in_the_schema_registry(self):
        for capability, config in CAPABILITY_REGISTRY.items():
            with self.subTest(capability=capability):
                self.assertIsNotNone(get_schema(config.response_schema_id, config.response_schema_version))


class LoadGoldenCasesTests(SimpleTestCase):
    def test_missing_dataset_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_golden_cases(capability="x", dataset="no_such_dataset", version="v1")

    def test_every_capability_has_at_least_one_golden_case(self):
        for capability in ALL_CAPABILITIES:
            with self.subTest(capability=capability):
                cases = load_golden_cases_for_capability(capability)
                self.assertGreater(len(cases), 0)

    def test_case_ids_are_unique_within_a_dataset(self):
        for capability in ALL_CAPABILITIES:
            with self.subTest(capability=capability):
                cases = load_golden_cases_for_capability(capability)
                case_ids = [c.case_id for c in cases]
                self.assertEqual(len(case_ids), len(set(case_ids)))

    def test_load_all_golden_cases_covers_every_capability(self):
        cases = load_all_golden_cases()
        capabilities_seen = {c.capability for c in cases}
        self.assertEqual(capabilities_seen, set(ALL_CAPABILITIES))

    def test_every_case_s_expected_response_matches_its_own_schema(self):
        # Golden fixtures authored against a schema that has since drifted
        # would be a self-inconsistent fixture -- catch that here, at fixture
        # load/validation time, independent of the full runner pipeline.
        for case in load_all_golden_cases():
            with self.subTest(case_id=case.case_id):
                schema = get_schema(case.response_schema_id, case.response_schema_version)
                _parsed, valid = validate_response(json.dumps(case.expected_response), schema)
                self.assertTrue(valid, f"{case.case_id}'s expected_response fails its own schema")


class GoldenFixtureHashSelfConsistencyTests(TestCase):
    """Proves every fixture's recorded snapshot hashes actually match a
    LIVE render of its own prompt_name/template_vars right now -- this is
    the exact mechanism EvaluationRunner's prompt-regression detection
    depends on (see tests_runner.py), verified independently here against
    every real golden fixture, not just a synthetic example."""

    def test_every_golden_case_hash_matches_a_fresh_render(self):
        for case in load_all_golden_cases():
            with self.subTest(case_id=case.case_id):
                rendered = render_prompt(
                    case.prompt_name, case.template_vars,
                    response_schema_id=case.response_schema_id,
                    response_schema_version=case.response_schema_version,
                )
                self.assertEqual(rendered.template_hash, case.expected_prompt_template_hash)
                self.assertEqual(rendered.rendered_input_hash, case.expected_rendered_input_hash)
