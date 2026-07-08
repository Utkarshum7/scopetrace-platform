"""
Phase 7b -- read-only serializers for AI output. Phase 7c adds
AIFactorRecommendationSerializer. apps.ingestion.views imports both
(apps.ingestion depending on apps.ai is the correct direction per ADR 0006
-- AI reads governed context, never the reverse; nothing in apps.ai
imports apps.ingestion's serializers).
"""
from rest_framework import serializers

from apps.ai.models import AIAnnotation, AIFactorRecommendation


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
