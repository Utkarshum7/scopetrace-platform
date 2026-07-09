"""
Phase 7.5 (H2, Finding 3) -- the gateway idempotency short-circuit must
REPLAY the original parsed result on a redelivered call, not return
parsed=None. This is what lets a capability service recover the AI output
after a worker crash between the gateway's OK write and its own downstream
persistence.
"""
from django.test import TestCase, override_settings

from apps.ai.models import AIAnnotation, AIInteraction, TenantAIPolicy
from apps.ai.providers.echo import canned
from apps.ai.services.gateway import invoke_ai
from apps.core.models import Organization


def _enable_ai(org, **overrides):
    defaults = {"ai_enabled": True, "provider_override": "echo", "model_override": "echo-1"}
    defaults.update(overrides)
    return TenantAIPolicy.objects.create(organization=org, **defaults)


def _valid_echo_value(echo_text="hi"):
    return canned({"acknowledged": True, "echo": echo_text})


@override_settings(AI_ENABLED=True, AI_PROVIDER="echo", AI_DEFAULT_MODEL="echo-1")
class IdempotentReplayTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Replay Org")
        _enable_ai(self.org)

    def _call(self, key):
        return invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value("hi")},
            response_schema_id="foundation.selftest", response_schema_version=1,
            idempotency_key=key,
        )

    def test_replay_returns_the_same_parsed_body_not_none(self):
        first = self._call("job-replay")
        self.assertEqual(first.outcome, "OK")
        self.assertEqual(first.parsed, {"acknowledged": True, "echo": "hi"})

        second = self._call("job-replay")  # redelivery / duplicate
        # Same interaction, no new provider call...
        self.assertEqual(first.interaction_id, second.interaction_id)
        self.assertEqual(AIInteraction.objects.filter(idempotency_key="job-replay").count(), 1)
        # ...but crucially the parsed body is REPLAYED, not lost.
        self.assertEqual(second.parsed, first.parsed)
        self.assertEqual(second.raw_text, first.raw_text)

    def test_idempotent_call_persists_response_text_for_replay(self):
        self._call("job-persist")
        row = AIInteraction.objects.get(idempotency_key="job-persist")
        self.assertTrue(row.response_text, "idempotent OK call must persist its raw response for replay")

    def test_non_idempotent_call_stays_hashes_only(self):
        invoke_ai(
            organization=self.org, capability="foundation.selftest",
            prompt_name="foundation.selftest", template_vars={"echo_value": _valid_echo_value("hi")},
            response_schema_id="foundation.selftest", response_schema_version=1,
        )
        row = AIInteraction.objects.filter(idempotency_key="").latest("created_at")
        self.assertEqual(row.response_text, "", "non-idempotent call must NOT persist response body")
        self.assertTrue(row.response_hash, "but the hash is still recorded")

    def test_capability_service_recovers_after_simulated_crash_before_persistence(self):
        """The real Finding-3 scenario: the gateway OK row exists but the
        capability's own downstream row was never written (worker crashed in
        between). The redelivered capability call must still produce the
        artifact, because replay hands back the parsed body."""
        import json

        from apps.ai.services import anomaly_detection
        from apps.core.models import DataSource
        from apps.ingestion.models import EmissionRecord, UploadBatch

        valid = {
            "explanation": "Quantity is far above the batch median.",
            "contributing_factors": ["bulk purchase"],
            "confidence": "HIGH",
            "suggested_investigation": "Confirm with the site's fuel log.",
        }
        ds = DataSource.objects.create(
            organization=self.org, name="src", source_type=DataSource.SourceType.SAP_FUEL,
        )
        batch = UploadBatch.objects.create(organization=self.org, data_source=ds, file_name="f.csv")
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=0, raw_data_payload={"qty": "999999"},
            status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
            scope_category="SCOPE_1", normalized_value="999999", normalized_unit="L",
            validation_errors={"quantity": ["outlier"]},
        )
        key = f"anomaly_detection:{record.id}"

        # Reproduce the exact crash-before-persistence state: a prior OK
        # interaction (with its response persisted for replay) exists, but the
        # downstream AIAnnotation was NEVER written -- the worker died between
        # the two.
        AIInteraction.objects.create(
            organization=self.org, capability="anomaly_detection", provider="echo",
            model_id="echo-1", outcome=AIInteraction.Outcome.OK,
            egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
            idempotency_key=key, response_text=json.dumps(valid),
            parameters={"response_schema_id": "anomaly_detection", "response_schema_version": 2},
        )
        self.assertEqual(AIAnnotation.objects.filter(record=record).count(), 0)

        # Redelivery: pre-7.5 the short-circuit returned parsed=None and the
        # annotation was lost forever. Now replay recovers it.
        recovered = anomaly_detection.generate_anomaly_explanation(record)
        self.assertIsNotNone(recovered, "redelivered call must recover the annotation, not drop it")
        self.assertEqual(recovered.explanation, valid["explanation"])
        self.assertEqual(AIAnnotation.objects.filter(record=record).count(), 1)
        # Still exactly one paid interaction -- replay did NOT re-call the provider.
        self.assertEqual(AIInteraction.objects.filter(idempotency_key=key, outcome="OK").count(), 1)
