"""
Phase 7b -- read-only serializers for AI output. apps.ingestion.views
imports AIAnnotationSerializer (apps.ingestion depending on apps.ai is the
correct direction per ADR 0006 -- AI reads governed context, never the
reverse; nothing in apps.ai imports apps.ingestion's serializers).
"""
from rest_framework import serializers

from apps.ai.models import AIAnnotation


class AIAnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIAnnotation
        fields = [
            "id", "capability", "explanation", "contributing_factors",
            "confidence", "suggested_investigation", "created_at",
        ]
        read_only_fields = fields
