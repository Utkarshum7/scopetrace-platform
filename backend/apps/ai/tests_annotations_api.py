"""
Phase 7b -- GET /api/records/{id}/ai-annotations/ tests. Read-only:
explicitly proves no mutation verb (POST/PUT/PATCH/DELETE) is accepted on
this path, in addition to the normal read-path coverage.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status as drf_status
from rest_framework.test import APIClient

from apps.accounts.models import Membership, Role
from apps.ai.models import AIAnnotation, AIInteraction, TenantAIPolicy
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch

User = get_user_model()


def _make_batch(org, ds):
    return UploadBatch.objects.create(organization=org, data_source=ds, file_name="annotations_api_test.csv")


def _make_record(org, batch, **extra):
    defaults = dict(
        organization=org, batch=batch, row_index=1, raw_data_payload={"a": 1},
        status=EmissionRecord.RecordStatus.SUSPICIOUS, is_suspicious=True,
        normalized_value=500, normalized_unit="L", scope_category="SCOPE_1",
    )
    defaults.update(extra)
    return EmissionRecord.objects.create(**defaults)


def _make_interaction(org):
    return AIInteraction.objects.create(
        organization=org, capability="anomaly_detection", provider="echo", model_id="echo-1",
        outcome=AIInteraction.Outcome.OK, egress_tier_applied=TenantAIPolicy.EgressTier.REDACTED,
    )


class AIAnnotationsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name="Annotations API Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = _make_batch(self.org, self.ds)
        self.record = _make_record(self.org, self.batch)
        self.analyst = self._user("annotations_analyst", Role.ANALYST)

    def _user(self, name, role):
        u = User.objects.create_user(name, password="pw")
        Membership.objects.create(user=u, organization=self.org, role=role, active=True)
        return u

    def test_empty_list_when_no_annotations_exist(self):
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json(), [])

    def test_returns_annotations_newest_first(self):
        import time

        first = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="first",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        # A real time gap, not just call-order -- some platforms' clock
        # resolution is coarse enough that two immediate creates can land
        # on the identical created_at value, making "-created_at" ordering
        # genuinely ambiguous between them (a real environment
        # characteristic, not a code bug).
        time.sleep(0.01)
        second = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="second",
            confidence=AIAnnotation.Confidence.HIGH, suggested_investigation="y",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["id"], str(second.id))
        self.assertEqual(body[1]["id"], str(first.id))

    def test_response_shape_includes_all_advisory_fields(self):
        AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="explained",
            contributing_factors=["factor a", "factor b"],
            confidence=AIAnnotation.Confidence.MEDIUM, suggested_investigation="check the log",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        body = response.json()[0]
        self.assertEqual(body["explanation"], "explained")
        self.assertEqual(body["contributing_factors"], ["factor a", "factor b"])
        self.assertEqual(body["confidence"], "MEDIUM")
        self.assertEqual(body["suggested_investigation"], "check the log")
        self.assertEqual(body["capability"], "ANOMALY_DETECTION")
        self.assertIn("created_at", body)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        self.assertIn(response.status_code, (drf_status.HTTP_401_UNAUTHORIZED, drf_status.HTTP_403_FORBIDDEN))

    def test_cross_tenant_record_is_not_reachable(self):
        # TenantScopedViewSetMixin's get_queryset() is already org-scoped,
        # so a cross-tenant id is never in the visible queryset at all --
        # get_object() 404s before any object-level permission check runs.
        # Same behavior as the existing /versions/ endpoints this action
        # mirrors -- matches, doesn't invent a new convention.
        other_org = Organization.objects.create(name="Other Annotations Org")
        other_user = User.objects.create_user("other_annotations_user", password="pw")
        Membership.objects.create(user=other_user, organization=other_org, role=Role.ANALYST, active=True)
        self.client.force_authenticate(other_user)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_no_mutation_verb_is_accepted_on_this_path(self):
        self.client.force_authenticate(self.analyst)
        url = f"/api/records/{self.record.id}/ai-annotations/"
        for method in (self.client.post, self.client.put, self.client.patch, self.client.delete):
            with self.subTest(method=method):
                response = method(url, data={}, format="json")
                self.assertEqual(response.status_code, drf_status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_deleted_record_returns_404(self):
        from apps.ingestion.services.soft_delete import soft_delete_record

        soft_delete_record(record=self.record, actor=self.analyst, reason="test cleanup")
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        self.assertEqual(response.status_code, drf_status.HTTP_404_NOT_FOUND)

    def test_validation_assistance_annotations_surface_through_the_same_endpoint(self):
        # Phase 7d reuses AIAnnotation (see ADR 0011) rather than adding a
        # second endpoint -- this endpoint already returns every
        # capability's rows; the frontend is what splits them into
        # sections by `capability`.
        AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE,
            explanation="Quantity is negative.",
            contributing_factors=["quantity"],
            confidence=AIAnnotation.Confidence.MEDIUM,
            suggested_investigation="Re-enter with the correct sign.",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["capability"], "VALIDATION_ASSISTANCE")
        self.assertEqual(body[0]["contributing_factors"], ["quantity"])
        self.assertEqual(body[0]["suggested_investigation"], "Re-enter with the correct sign.")

    def test_both_capabilities_returned_together_newest_first(self):
        import time

        anomaly = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.ANOMALY_DETECTION, explanation="anomaly",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="x",
        )
        time.sleep(0.01)
        validation = AIAnnotation.objects.create(
            organization=self.org, record=self.record, interaction=_make_interaction(self.org),
            capability=AIAnnotation.Capability.VALIDATION_ASSISTANCE, explanation="validation",
            confidence=AIAnnotation.Confidence.LOW, suggested_investigation="y",
        )
        self.client.force_authenticate(self.analyst)
        response = self.client.get(f"/api/records/{self.record.id}/ai-annotations/")
        body = response.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["id"], str(validation.id))
        self.assertEqual(body[1]["id"], str(anomaly.id))
