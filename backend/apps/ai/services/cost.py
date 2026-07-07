"""
Token-based cost estimation + per-organization monthly budget check. Pricing
is a small, explicitly-editable table -- real-world $/1K-token pricing
changes over time, so this is intentionally hand-maintained data, never
fetched from a vendor pricing API (that would be its own egress/reliability
surface for no benefit at this scale).
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from apps.ai.models import AIInteraction

# USD per 1,000 tokens: (provider, model) -> (input_price, output_price).
# Platform defaults only -- a TenantAIPolicy's model_override does not get
# its own pricing row automatically; unknown (provider, model) pairs fall
# back to _DEFAULT_PRICE_PER_1K (a conservative Claude-Sonnet-tier estimate)
# so an unrecognized model never silently prices as free.
PRICING_USD_PER_1K_TOKENS = {
    ("anthropic", "claude-sonnet-5"): (Decimal("0.003"), Decimal("0.015")),
    ("openai", "gpt-4o"): (Decimal("0.0025"), Decimal("0.010")),
    ("echo", "echo-1"): (Decimal("0"), Decimal("0")),
}
_DEFAULT_PRICE_PER_1K = (Decimal("0.003"), Decimal("0.015"))


def estimate_cost_usd(provider: str, model: str, input_tokens: int | None, output_tokens: int | None) -> Decimal:
    input_price, output_price = PRICING_USD_PER_1K_TOKENS.get((provider, model), _DEFAULT_PRICE_PER_1K)
    input_cost = (Decimal(input_tokens or 0) / Decimal(1000)) * input_price
    output_cost = (Decimal(output_tokens or 0) / Decimal(1000)) * output_price
    return (input_cost + output_cost).quantize(Decimal("0.000001"))


@dataclass(frozen=True)
class BudgetStatus:
    ok: bool
    spent_usd: Decimal
    budget_usd: Decimal


def check_budget(organization, monthly_budget_usd: Decimal) -> BudgetStatus:
    """Sums AIInteraction.cost_usd for this org since the start of the
    current calendar month. Counts every interaction with a recorded cost
    (cost_usd is only ever set once a provider call actually completed and
    returned token counts -- see gateway.py) regardless of outcome: a
    SCHEMA_INVALID response still consumed real, billable provider tokens,
    so it must still count against budget even though its output was
    discarded as unusable.
    """
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = (
        AIInteraction.objects.filter(
            organization=organization,
            created_at__gte=month_start,
            cost_usd__isnull=False,
        ).aggregate(total=Sum("cost_usd"))["total"]
        or Decimal("0")
    )
    return BudgetStatus(ok=spent < monthly_budget_usd, spent_usd=spent, budget_usd=monthly_budget_usd)
