"""
Phase 7g -- centralized AI operational metrics. Platform-wide only (no
`organization` parameter): cross-tenant AI usage/health is a platform-
engineering concern, not tenant data, mirroring how
apps.carbon.metrics_views.PlatformMetricsView is IsPlatformAdmin-only
and apps.ai.evaluation.models' own docstring already establishes
EvaluationRun/EvaluationResult as platform-level artifacts with no
`organization` FK at all.

Built entirely from data this codebase already writes -- AIInteraction
(every gateway call, see apps.ai.services.gateway) and EvaluationRun/
EvaluationResult (every Tier 1/Tier 2 evaluation run) -- no new
accounting, no duplicate bookkeeping. See ADR 0014.
"""
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate

from apps.ai.evaluation.models import EvaluationResult, EvaluationRun
from apps.ai.models import AIInteraction
from apps.ai.services.cache_metrics import get_cache_hit_count

_ZERO = Decimal("0")


def _requests_summary(qs) -> dict:
    total = qs.count()
    by_outcome = {row["outcome"]: row["n"] for row in qs.values("outcome").annotate(n=Count("id"))}
    failed = total - by_outcome.get(AIInteraction.Outcome.OK, 0)
    return {"total": total, "by_outcome": by_outcome, "failed": failed}


def _latency_summary(qs) -> dict:
    with_latency = qs.filter(latency_ms__isnull=False)
    agg = with_latency.aggregate(avg=Avg("latency_ms"))
    # Daily-bucketed trend (TruncDate, mirroring apps.carbon.services.metrics'
    # own TruncMonth/Quarter/Year pattern for the calc-metrics timeseries) --
    # real per-day averages from AIInteraction.latency_ms, not interpolated.
    trend = (
        with_latency.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(avg_ms=Avg("latency_ms"))
        .order_by("day")
    )
    return {
        "avg_ms": round(agg["avg"], 1) if agg["avg"] is not None else None,
        "trend": [
            {"date": row["day"].isoformat(), "avg_ms": round(row["avg_ms"], 1)} for row in trend
        ],
    }


def _provider_usage(qs) -> dict:
    return {row["provider"]: row["n"] for row in qs.values("provider").annotate(n=Count("id"))}


def _capability_usage(qs) -> dict:
    return {row["capability"]: row["n"] for row in qs.values("capability").annotate(n=Count("id"))}


def _token_and_cost_summary(qs) -> dict:
    agg = qs.aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"), cost_usd=Sum("cost_usd"),
    )
    # SQLite's SUM() aggregate doesn't preserve DecimalField's exact scale
    # the way Postgres does -- quantize explicitly to cost_usd's own
    # decimal_places (6) so the formatted string is consistent regardless
    # of backend.
    cost = (agg["cost_usd"] or _ZERO).quantize(Decimal("0.000001"))
    return {
        "input_tokens": agg["input_tokens"] or 0,
        "output_tokens": agg["output_tokens"] or 0,
        "estimated_cost_usd": str(cost),
    }


def evaluation_summary() -> dict:
    """Latest run per tier, plus a recent-regression breakdown -- reuses
    EvaluationRun/EvaluationResult's own persisted fields directly, no
    second implementation of "did the suite pass." "Invariant failures"
    (the I1-I6 suite in apps.ai.evaluation.tests_invariants) has no
    persistence layer of its own -- it's a regular Django TestCase suite,
    enforced as a CI merge gate, not a runtime-tracked metric -- so it is
    deliberately reported here as a static pointer, not fabricated trend
    data.
    """
    latest_by_tier = {}
    for tier, _ in EvaluationRun.Tier.choices:
        run = EvaluationRun.objects.filter(tier=tier).order_by("-started_at").first()
        if run is None:
            latest_by_tier[tier] = None
            continue
        latest_by_tier[tier] = {
            "id": str(run.id),
            "status": run.status,
            "trigger": run.trigger,
            "total_cases": run.total_cases,
            "passed_cases": run.passed_cases,
            "failed_cases": run.failed_cases,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        }

    recent_runs_list = list(EvaluationRun.objects.order_by("-started_at")[:10])
    recent_run_ids = [run.id for run in recent_runs_list]
    recent_results = EvaluationResult.objects.filter(run_id__in=recent_run_ids)
    outcome_breakdown = {
        row["outcome"]: row["n"] for row in recent_results.values("outcome").annotate(n=Count("id"))
    }

    # Real per-run pass/fail trend (oldest-first, so a chart can plot it
    # left-to-right) -- not fabricated: each entry is one actually-persisted
    # EvaluationRun, not an interpolated/bucketed synthetic point.
    recent_runs = [
        {
            "id": str(run.id), "tier": run.tier, "status": run.status,
            "passed_cases": run.passed_cases, "failed_cases": run.failed_cases,
            "started_at": run.started_at,
        }
        for run in reversed(recent_runs_list)
    ]

    return {
        "latest_by_tier": latest_by_tier,
        "recent_runs": recent_runs,
        "recent_outcome_breakdown": outcome_breakdown,
        "regressions": outcome_breakdown.get(EvaluationResult.Outcome.REGRESSION, 0),
        "schema_failures": outcome_breakdown.get(EvaluationResult.Outcome.SCHEMA_INVALID, 0),
        "replay_failures": outcome_breakdown.get(EvaluationResult.Outcome.PROVIDER_ERROR, 0),
        "invariant_suite": {
            "note": "Enforced as a CI merge gate (apps.ai.evaluation.tests_invariants), not a "
                    "runtime-tracked metric -- no historical pass/fail trend is persisted.",
        },
    }


def platform_ai_summary(filters=None) -> dict:
    """The full observability snapshot: requests, latency, failures,
    provider usage, replay usage, token usage, estimated cost, cache
    hits, and evaluation health. `filters` may include date_from/date_to
    (applied to AIInteraction.created_at) -- same optional-range shape
    every other Metrics-API-style summary in this codebase uses.
    """
    filters = filters or {}
    qs = AIInteraction.objects.all()
    if filters.get("date_from"):
        qs = qs.filter(created_at__gte=filters["date_from"])
    if filters.get("date_to"):
        qs = qs.filter(created_at__lte=filters["date_to"])

    provider_usage = _provider_usage(qs)

    return {
        "requests": _requests_summary(qs),
        "latency": _latency_summary(qs),
        "provider_usage": provider_usage,
        "replay_usage": provider_usage.get("replay", 0),
        "capability_usage": _capability_usage(qs),
        "tokens_and_cost": _token_and_cost_summary(qs),
        "cache_hits": get_cache_hit_count(),
        "evaluation": evaluation_summary(),
    }
