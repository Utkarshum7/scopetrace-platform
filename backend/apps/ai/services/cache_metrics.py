"""
Phase 7g -- a single, additive observability counter for invoke_ai()'s
idempotency short-circuit (apps.ai.services.gateway). A short-circuited
call returns the PRIOR AIInteraction's outcome without writing a new row
(see gateway.py's own docstring: "not a data cache -- a replayed call
returns the prior outcome... but no parsed body"), so it is otherwise
completely invisible to any AIInteraction-based metric. This mirrors
apps.ai.tasks.AI_HEARTBEAT_CACHE_KEY's exact pattern (a lightweight cache
counter, not a new model/migration) -- best-effort, resets on a cache
flush/restart, which is an acceptable trade-off for an observability
counter, the same way the heartbeat's own "stale means unknown" contract
already is.
"""
from django.core.cache import cache

AI_CACHE_HIT_COUNTER_KEY = "ai:metrics:cache_hits"


def record_cache_hit() -> None:
    """Called from invoke_ai()'s idempotency short-circuit, exactly once
    per redelivered/duplicate call that gets served from a prior
    AIInteraction instead of a fresh provider round trip."""
    try:
        cache.incr(AI_CACHE_HIT_COUNTER_KEY)
    except ValueError:
        # incr() raises ValueError if the key doesn't exist yet.
        cache.set(AI_CACHE_HIT_COUNTER_KEY, 1, timeout=None)


def get_cache_hit_count() -> int:
    return cache.get(AI_CACHE_HIT_COUNTER_KEY, 0)
