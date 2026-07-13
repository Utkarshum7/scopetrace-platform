"""
Phase 7b -- read-only serializers for AI output. Phase 7c adds
AIFactorRecommendationSerializer. Phase 7e adds AIConversationSerializer/
AIConversationMessageSerializer, used by apps.ai's own views (apps.ai.views)
-- the first Phase 7 capability with API views of its own, rather than
being surfaced through an existing apps.ingestion viewset action.
apps.ingestion.views imports the annotation/factor-recommendation
serializers (apps.ingestion depending on apps.ai is the correct direction
per ADR 0006 -- AI reads governed context, never the reverse; nothing in
apps.ai imports apps.ingestion's serializers).
"""
from rest_framework import serializers

from apps.ai.models import (
    AIAnnotation,
    AIConversation,
    AIConversationMessage,
    AIFactorRecommendation,
    AIReportNarration,
)


class AIAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAnnotation
        fields = [
            "id", "capability", "explanation", "contributing_factors",
            "confidence", "suggested_investigation", "created_at",
        ]
        read_only_fields = fields


class AIFactorRecommendationSerializer(serializers.ModelSerializer):
    # AIFactorRecommendation.recommended_factor is a raw FK -- the milestone
    # asks for a human-readable "recommended factor", not an id, so this
    # computes one from the factor's dataset/region/value rather than
    # exposing the FK (or its pk) directly. None when the AI recommended
    # none of the candidates it was shown.
    recommended_factor_label = serializers.SerializerMethodField()

    class Meta:
        model = AIFactorRecommendation
        fields = [
            "id", "recommended_factor_label", "confidence", "explanation",
            "reasoning", "alternative_candidates", "created_at",
        ]
        read_only_fields = fields

    def get_recommended_factor_label(self, obj):
        factor = obj.recommended_factor
        if factor is None:
            return None
        if factor.region:
            region_code = factor.region.code
        elif factor.dataset.region:
            region_code = factor.dataset.region.code
        else:
            region_code = "GLOBAL"
        return f"{factor.dataset.publisher} {factor.dataset.version} ({region_code}) — {factor.co2e_per_unit} {factor.unit}"


class AIConversationMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversationMessage
        fields = [
            "id", "role", "content", "citations", "confidence",
            "unsupported_claim", "retrieved_context", "created_at",
        ]
        read_only_fields = fields


class AIConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = ["id", "created_at"]
        read_only_fields = fields


class AskQuestionSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=1, max_length=2000, trim_whitespace=True)


class AIReportNarrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIReportNarration
        fields = [
            "id", "date_from", "date_to", "scope", "executive_summary",
            "key_highlights", "trend_explanations", "recommendations",
            "confidence", "created_at",
        ]
        read_only_fields = fields


class RegenerateNarrationSerializer(serializers.Serializer):
    """Mirrors apps.carbon.report_views.ComplianceReportFilterSerializer's
    exact validation -- narration is always regenerated for the SAME kind
    of defined reporting period a compliance report covers, never
    open-ended."""
    date_from = serializers.DateField(required=True)
    date_to = serializers.DateField(required=True)
    scope = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        if data["date_from"] > data["date_to"]:
            raise serializers.ValidationError("date_from must not be after date_to.")
        return data
