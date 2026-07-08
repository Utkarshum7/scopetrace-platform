"""Phase 7a -- prompt registry + schema registry tests."""
import jsonschema
from django.test import TestCase

from apps.ai.models import AIPromptVersion
from apps.ai.prompts.registry import render_prompt
from apps.ai.schemas import get_schema, validate_response


class RenderPromptTests(TestCase):
    def test_renders_template_vars(self):
        rendered = render_prompt(
            "foundation.selftest", {"echo_value": "hello"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertIn("hello", rendered.text)
        self.assertNotIn("$echo_value", rendered.text)

    def test_missing_var_renders_literally_not_raises(self):
        # safe_substitute, not substitute -- a caller/template mismatch must
        # never crash the gateway call (fail-safe, invariant I6).
        rendered = render_prompt(
            "foundation.selftest", {},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertIn("$echo_value", rendered.text)

    def test_registers_a_prompt_version(self):
        rendered = render_prompt(
            "foundation.selftest", {"echo_value": "x"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertIsNotNone(rendered.prompt_version.id)
        self.assertEqual(rendered.prompt_version.name, "foundation.selftest")
        self.assertTrue(AIPromptVersion.objects.filter(pk=rendered.prompt_version.pk).exists())

    def test_same_template_reused_not_reregistered(self):
        render_prompt(
            "foundation.selftest", {"echo_value": "a"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        render_prompt(
            "foundation.selftest", {"echo_value": "b"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        # Different template_vars, SAME template file -> one AIPromptVersion row.
        self.assertEqual(AIPromptVersion.objects.filter(name="foundation.selftest").count(), 1)

    def test_different_template_vars_produce_different_rendered_input_hash(self):
        first = render_prompt(
            "foundation.selftest", {"echo_value": "a"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        second = render_prompt(
            "foundation.selftest", {"echo_value": "b"},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        self.assertEqual(first.template_hash, second.template_hash)
        self.assertNotEqual(first.rendered_input_hash, second.rendered_input_hash)

    def test_unknown_template_name_raises(self):
        with self.assertRaises(FileNotFoundError):
            render_prompt(
                "no.such.template", {},
                response_schema_id="x", response_schema_version=1,
            )


class SchemaRegistryTests(TestCase):
    def test_get_schema_returns_registered_schema(self):
        schema = get_schema("foundation.selftest", 1)
        self.assertEqual(schema["type"], "object")

    def test_unknown_schema_raises_key_error(self):
        with self.assertRaises(KeyError):
            get_schema("no.such.schema", 1)

    def test_foundation_selftest_schema_validates_correct_shape(self):
        schema = get_schema("foundation.selftest", 1)
        jsonschema.validate({"acknowledged": True, "echo": "hi"}, schema)

    def test_foundation_selftest_schema_rejects_extra_fields(self):
        schema = get_schema("foundation.selftest", 1)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"acknowledged": True, "echo": "hi", "extra": 1}, schema)

    def test_foundation_selftest_schema_rejects_missing_required_field(self):
        schema = get_schema("foundation.selftest", 1)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"acknowledged": True}, schema)

    def test_all_five_planned_capability_schemas_are_registered(self):
        # Phase 7a.5 -- eval-harness fixtures only, no real feature behind
        # any of these (see this module's own docstring).
        for schema_id in (
            "anomaly_detection", "factor_recommendation", "validation_assistance",
            "esg_assistant", "report_narration",
        ):
            self.assertIsNotNone(get_schema(schema_id, 1))


class ValidateResponseTests(TestCase):
    """The shared helper apps.ai.services.gateway.invoke_ai() and
    apps.ai.evaluation.runner.EvaluationRunner both use -- one
    implementation, proved correct once."""

    def test_valid_json_matching_schema_returns_parsed_and_true(self):
        schema = get_schema("foundation.selftest", 1)
        parsed, valid = validate_response('{"acknowledged": true, "echo": "hi"}', schema)
        self.assertTrue(valid)
        self.assertEqual(parsed, {"acknowledged": True, "echo": "hi"})

    def test_malformed_json_returns_none_and_false(self):
        schema = get_schema("foundation.selftest", 1)
        parsed, valid = validate_response("not json at all", schema)
        self.assertFalse(valid)
        self.assertIsNone(parsed)

    def test_valid_json_not_matching_schema_returns_none_and_false(self):
        schema = get_schema("foundation.selftest", 1)
        parsed, valid = validate_response('{"acknowledged": "not-a-bool"}', schema)
        self.assertFalse(valid)
        self.assertIsNone(parsed)
