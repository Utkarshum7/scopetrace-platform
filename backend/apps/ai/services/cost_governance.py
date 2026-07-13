"""
Phase 7g -- organization-level AI cost governance. Reuses
apps.ai.services.policy.resolve_policy() and apps.ai.services.cost.
check_budget() VERBATIM for budget utilization -- the exact same
resolution/aggregation the gateway itself uses on every call, not a
second implementation. See ADR 0014.
"""
from decimal import Decimal

from django.db.models import Count, Sum

from apps.ai.models import AIInteraction
from apps.ai.services.cost import check_budget
from apps.ai.services.policy import resolve_policy


def org_cost_summary(organization, filters=None) -> dict:
    """Token consumption, estimated spend, budget utilization, provider
    distribution, and capability distribution for one organization.
    `filters` may include date_from/date_to (applied to AIInteraction.
    created_at) for the usage breakdowns -- budget utilization is always
    the current-calendar-month figure check_budget() itself computes,
    independent of any date filter (a budget is inherently a monthly
    concept, not a report-period one).
    """
    filters = filters or {}
    qs = AIInteraction.objects.filter(organization=organization)
    if filters.get("date_from"):
        qs = qs.filter(created_at__gte=filters["date_from"])
    if filters.get("date_to"):
        qs = qs.filter(created_at__lte=filters["date_to"])

    token_and_cost = qs.aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"), cost_usd=Sum("cost_usd"),
    )
    # SQLite's SUM() aggregate doesn't preserve DecimalField's exact scale
    # the way Postgres does -- quantize explicitly to cost_usd's own
    # decimal_places (6) so the formatted string is consistent regardless
    # of backend.
    spend = (token_and_cost["cost_usd"] or Decimal("0")).quantize(Decimal("0.000001"))
    provider_distribution = {row["provider"]: row["n"] for row in qs.values("provider").annotate(n=Count("id"))}
    capability_distribution = {
        row["capability"]: row["n"] for row in qs.values("capability").annotate(n=Count("id"))
    }

    policy = resolve_policy(organization)
    budget = check_budget(organization, policy.monthly_budget_usd)
    utilization_pct = (
        round(float(budget.spent_usd / budget.budget_usd) * 100, 1) if budget.budget_usd > 0 else None
    )

    return {
        "ai_enabled": policy.ai_enabled,
        "token_consumption": {
            "input_tokens": token_and_cost["input_tokens"] or 0,
            "output_tokens": token_and_cost["output_tokens"] or 0,
        },
        "estimated_spend_usd": str(spend),
        "budget": {
            # Quantized here too (check_budget() itself returns whatever
            # precision SQLite's SUM() happened to produce -- see the
            # comment above on `spend` for why this codebase's SQLite test
            # backend doesn't preserve DecimalField's exact scale).
            "spent_usd": str(budget.spent_usd.quantize(Decimal("0.000001"))),
            "budget_usd": str(budget.budget_usd.quantize(Decimal("0.01"))),
            "utilization_pct": utilization_pct,
            "over_budget": not budget.ok,
        },
        "provider_distribution": provider_distribution,
        "capability_distribution": capability_distribution,
    }
