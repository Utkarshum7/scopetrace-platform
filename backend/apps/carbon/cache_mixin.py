"""
Caching for global reference-data list endpoints (activity types, factor
datasets, factors). Caches the serialized list AFTER DRF permission checks (so
it can't leak to unauthorized callers), keyed by a global version that is bumped
whenever factors are imported.
"""
from django.core.cache import cache
from rest_framework.response import Response

_VERSION_KEY = "refdata:version"
_DEFAULT_TTL = 300


def bump_refdata_version():
    """Invalidate all cached reference lists (call after a factor import)."""
    try:
        return cache.incr(_VERSION_KEY)
    except ValueError:
        cache.set(_VERSION_KEY, 1, None)
        return 1


def _refdata_version():
    version = cache.get(_VERSION_KEY)
    if version is None:
        cache.set(_VERSION_KEY, 1, None)
        return 1
    return version


class CachedReferenceListMixin:
    reference_cache_ttl = _DEFAULT_TTL

    def list(self, request, *args, **kwargs):
        key = (
            f"refdata:{self.__class__.__name__}:v{_refdata_version()}:"
            f"{request.get_full_path()}"
        )
        cached = cache.get(key)
        if cached is not None:
            return Response(cached)
        response = super().list(request, *args, **kwargs)
        cache.set(key, response.data, self.reference_cache_ttl)
        return response
