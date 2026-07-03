from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.accounts.models import Membership

User = get_user_model()


class MembershipSerializer(serializers.ModelSerializer):
    organization_id = serializers.UUIDField(source="organization.id", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = Membership
        fields = ["id", "organization_id", "organization_name", "role", "active"]


class UserSerializer(serializers.ModelSerializer):
    is_platform_admin = serializers.BooleanField(source="is_superuser", read_only=True)
    memberships = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_platform_admin",
            "memberships",
        ]

    def get_memberships(self, obj):
        qs = obj.memberships.filter(active=True).select_related("organization")
        return MembershipSerializer(qs, many=True).data


class LoginSerializer(TokenObtainPairSerializer):
    """Standard obtain-pair, augmented with the authenticated user's profile
    and active memberships so the client can render role-aware UI immediately."""

    def validate(self, attrs):
        data = super().validate(attrs)
        data["user"] = UserSerializer(self.user).data
        return data
