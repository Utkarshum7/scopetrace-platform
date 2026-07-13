"""
Phase 7c -- GET /api/records/{id}/factor-recommendations/ tests. Read-only:
explicitly proves no mutation verb (POST/PUT/PATCH/DELETE) is accepted on
this path, in addition to the normal read-path coverage. Mirrors
tests_annotations_api.py's exact structure.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.ai.models import AIFactorRecommendation, AIInteraction, TenantAIPolicy
from apps.carbon.tests.factories import activity_type, dataset, factor
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="factor_rec_api_test.csv")


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.DRAFT, normalized_value=500,
        normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="factor_recommendation", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIFactorRecommendationsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Factor Rec API Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.activity_type = activity_type()
        self.dataset = dataset()
        self.factor = factor(self.dataset, self.activity_type)
        self.analyst = self._user("factor_rec_analyst", Role.ANALYST)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_empty_list_when_no_recommendations_exist(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_returns_recommendations_newest_first(self):
        import time

        first = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.LOW,
            explanation="first", reasoning="x",
        )
        # A real time gap, not just call-order -- some platforms' clock
        # resolution is coarse enough that two immediate creates can land
        # on the identical created_at value, making "-created_at" ordering
        # genuinely ambiguous between them (a real environment
        # characteristic, not a code bug).
        time.sleep(0.01)
        second = AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.HIGH,
            explanation="second", reasoning="y",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["id"], str(second.id))
        self.assertEqual(body[1]["id"], str(first.id))

    def test_response_shape_includes_all_advisory_fields(self):
        AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            recommended_factor=self.factor, confidence=AIFactorRecommendation.Confidence.MEDIUM,
            explanation="explained", reasoning="reasoned",
            alternative_candidates=["candidate_2"],
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        body = response.json()[0]
        self.assertEqual(body["explanation"], "explained")
        self.assertEqual(body["reasoning"], "reasoned")
        self.assertEqual(body["alternative_candidates"], ["candidate_2"])
        self.assertEqual(body["confidence"], "MEDIUM")
        self.assertIn(self.dataset.publisher, body["recommended_factor_label"])
        self.assertIn("created_at", body)
        # Only the human-readable "recommended_factor_label" is exposed --
        # never the raw "recommended_factor" FK id/UUID.
        self.assertNotIn("recommended_factor", body)

    def test_no_recommended_factor_serializes_as_null_label(self):
        AIFactorRecommendation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            recommended_factor=None, confidence=AIFactorRecommendation.Confidence.LOW,
            explanation="none fit", reasoning="x",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        body = response.json()[0]
        self.assertIsNone(body["recommended_factor_label"])

    def test_unauthenticated_request_rejected(self):
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        self.assertIn(response.status_code, (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN))

    def test_cross_tenant_record_is_not_reachable(self):
        # TenantScopedViewSetMixin's get_queryset() is already org-scoped,
        # so a cross-tenant id is never in the visible queryset at all --
        # get_object() 404s before any object-level permission check runs.
        # Same behavior as /versions/ and /ai-annotations/, which this
        # action mirrors -- matches, doesn't invent a new convention.
        other_org = Organization.objects.create(name="Other Factor Rec Org")
        other_user = User.objects.create_user("other_factor_rec_user", password="pw")
        Membership.objects.create(user=other_user, organization=other_org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(other_user)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_no_mutation_verb_is_accepted_on_this_path(self):
        self.client.force_authenticate(self.analyst)
        url = f"/api/records/{self.record.id}/factor-recommendations/"
        for method in (self.client.post, self.client.put, self.client.patch, self.client.delete):
            with self.subTest(method=method):
                response = method(url, data={}, format="json")
                self.assertEqual(response.status_code, drf_status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_deleted_record_returns_404(self):
        from apps.ingestion.services.soft_delete import soft_delete_record

        soft_delete_record(record=self.record, actor=self.analyst, reason="test cleanup")
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/factor-recommendations/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)
