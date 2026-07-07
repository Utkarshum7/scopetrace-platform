import logging

from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken, TokenError
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.accounts.serializers import LoginSerializer, UserSerializer
from apps.accounts.tenancy import resolve_tenant_context

logger = logging.getLogger(__name__)


class LoginView(TokenObtainPairView):
    """POST username/password -> {access, refresh, user}."""
    serializer_class = LoginSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request, *args, **kwargs):
        # Phase 6f: a WARNING per failed attempt (username + remote
        # address, never the password) -- baseline observability for
        # credential-stuffing/brute-force detection. Response body/status
        # code are completely unchanged; SimpleJWT's own error message is
        # already non-enumerating ("No active account found with the
        # given credentials" regardless of whether the username exists).
        try:
            return super().post(request, *args, **kwargs)
        except AuthenticationFailed:
            logger.warning(
                "Failed login attempt for username=%r from %s",
                request.data.get("username"), request.META.get("REMOTE_ADDR"),
            )
            raise


class RefreshView(TokenRefreshView):
    """POST {refresh} -> {access, refresh}. Rotates and blacklists the old
    refresh token (ROTATE_REFRESH_TOKENS + BLACKLIST_AFTER_ROTATION)."""
    permission_classes = [AllowAny]


class LogoutView(APIView):
    """POST {refresh} -> 205. Blacklists the refresh token (logout)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"detail": "A 'refresh' token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            return Response(
                {"detail": "Invalid or expired refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MeView(APIView):
    """GET -> current user, active memberships, resolved active organization."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = UserSerializer(request.user).data
        try:
            ctx = resolve_tenant_context(request)
            data["active_organization"] = (
                {"id": str(ctx.organization.id), "name": ctx.organization.name}
                if ctx.organization
                else None
            )
            data["active_role"] = ctx.role
            data["is_platform_admin"] = ctx.is_platform_admin
        except PermissionDenied:
            # A user with no active membership can still see their own profile.
            data["active_organization"] = None
            data["active_role"] = None
            data["is_platform_admin"] = bool(request.user.is_superuser)
        return Response(data)
