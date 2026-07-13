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
from django.db import transaction

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
    """Invalidate all cached metrics for an org -- call after any write that
    changes calculation data (ingest, recalc, backfill, soft-delete/restore).

    Phase 7.5 (H3): the actual cache mutation is deferred to
    transaction.on_commit(), regardless of whether the caller happens to be
    inside an open transaction.atomic() or not. This closes a real race: the
    Redis increment used to run at the exact call site, which is NOT part of
    the enclosing DB transaction -- when a caller invoked this from inside a
    still-open atomic() block (several call sites did), a concurrent metrics
    read could observe the bumped version, compute metrics against the
    NOT-YET-COMMITTED (thus not-yet-visible) calculation rows, and cache that
    stale/incomplete snapshot under the new version key for up to
    DEFAULT_TTL. Deferring to on_commit() makes the bump happen only once the
    underlying data is durably visible to every other connection, and makes
    it correct-by-construction at every current AND future call site -- no
    caller has to remember to bump "after" its own atomic block closes.

    A useful side effect: if the enclosing transaction rolls back, this
    on_commit callback never runs at all, so a failed write no longer
    triggers a pointless (if harmless) cache invalidation either.

    If there is no open transaction, Django runs the callback immediately --
    identical to the pre-7.5 behavior for callers outside a transaction.
    """
    def _do_bump():
        key = _version_key(org_id)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, _VERSION_TTL)

    transaction.on_commit(_do_bump)


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
