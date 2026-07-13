"""
Per-organization AI policy resolution -- overlays TenantAIPolicy (if any) on
top of platform defaults from settings. A missing TenantAIPolicy row, or one
with ai_enabled=False, always resolves to disabled: an org must explicitly
opt in, never inherit "the platform default provider is on" implicitly.
This is what apps.ai.services.gateway.invoke_ai() calls first on every
invocation.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings

from apps.ai.models import TenantAIPolicy


@dataclass(frozen=True)
class ResolvedAIPolicy:
    ai_enabled: bool
    provider: str
    model: str
    monthly_budget_usd: Decimal
    egress_tier: str
    byo_api_key_ref: str


def _disabled_policy() -> ResolvedAIPolicy:
    return ResolvedAIPolicy(
        ai_enabled=False,
        provider=settings.AI_PROVIDER,
        model=settings.AI_DEFAULT_MODEL,
        monthly_budget_usd=Decimal(str(settings.AI_DEFAULT_MONTHLY_BUDGET_USD)),
        egress_tier=settings.AI_DEFAULT_EGRESS_TIER,
        byo_api_key_ref="",
    )


def resolve_policy(organization) -> ResolvedAIPolicy:
    """Resolution order: global kill switch, then per-tenant policy row,
    then platform defaults for anything the tenant didn't override."""
    if not settings.AI_ENABLED:
        return _disabled_policy()

    tenant_policy = TenantAIPolicy.objects.filter(organization=organization).first()
    if tenant_policy is None or not tenant_policy.ai_enabled:
        return _disabled_policy()

    return ResolvedAIPolicy(
        ai_enabled=True,
        provider=tenant_policy.provider_override or settings.AI_PROVIDER,
        model=tenant_policy.model_override or settings.AI_DEFAULT_MODEL,
        monthly_budget_usd=(
            tenant_policy.monthly_budget_usd
            if tenant_policy.monthly_budget_usd is not None
            else Decimal(str(settings.AI_DEFAULT_MONTHLY_BUDGET_USD))
        ),
        egress_tier=tenant_policy.egress_tier,
        byo_api_key_ref=tenant_policy.byo_api_key_ref,
    )
