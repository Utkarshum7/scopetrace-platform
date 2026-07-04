"""
Metrics caching with per-organization version invalidation.

Every metrics cache key embeds the organization's current `calc_version`. Any
write that changes calculations (ingest / recalc / backfill) bumps that version,
so all of that org's cached metrics become unreachable at once — no key-tracking
or explicit deletes. Uses Django's cache (Redis when REDIS_URL is set, otherwise
the local-memory default).
"""
import hashlib
import json

from django.core.cache import cache

_VERSION_TTL = None      # version keys never expire on their own
DEFAULT_TTL = 300        # metrics payloads cached for 5 minutes


def _version_key(org_id):
    return f"metrics:calcver:{org_id}"


def get_calc_version(org_id):
    key = _version_key(org_id)
    version = cache.get(key)
    if version is None:
        version = 1
        cache.set(key, version, _VERSION_TTL)
    return version


def bump_calc_version(org_id):
    """Invalidate all cached metrics for an org (call after any calc write)."""
    key = _version_key(org_id)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, _VERSION_TTL)
        return 1


def cache_key(org_id, endpoint, params):
    version = get_calc_version(org_id)
    raw = json.dumps(params or {}, sort_keys=True, default=str)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]
    return f"metrics:{endpoint}:{org_id}:v{version}:{digest}"


def cached(org_id, endpoint, params, producer, timeout=DEFAULT_TTL):
    """Return a cached metrics payload or compute + store it."""
    key = cache_key(org_id, endpoint, params)
    value = cache.get(key)
    if value is None:
        value = producer()
        cache.set(key, value, timeout)
    return value
