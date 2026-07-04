"""
Activity-type and emission-factor resolution.

Both resolvers preload their reference data once so a whole batch of records can
be resolved in memory with no per-row query (N+1-free, 1M-record friendly).
"""
from collections import defaultdict

from apps.carbon.models import (
    ActivityMapping,
    EmissionFactor,
    EmissionFactorDataset,
)

GLOBAL = "GLOBAL"


class ActivityTypeResolver:
    """Maps (data_source_type, match_keys) -> ActivityType via ActivityMapping.

    Tries each candidate match_key (most specific first), then the source-type
    default (blank match_key). Case-insensitive on match keys.
    """

    def __init__(self):
        self._map = None

    def _load(self):
        if self._map is None:
            self._map = {}
            for m in ActivityMapping.objects.select_related("activity_type").all():
                self._map[(m.data_source_type, (m.match_key or "").upper())] = m.activity_type
        return self._map

    def resolve(self, source_type: str, match_keys=None):
        table = self._load()
        candidates = [(k or "").upper() for k in (match_keys or [])] + [""]
        for key in candidates:
            activity_type = table.get((source_type, key))
            if activity_type is not None:
                return activity_type
        return None


class FactorIndex:
    """
    Preloaded index of ACTIVE emission factors for in-memory resolution.

    Resolution is effective-dated (the activity's own date must fall in the
    factor's validity window) and specificity-ranked with a total ordering so
    the same inputs always select the same factor.
    """

    def __init__(self, activity_type_ids=None):
        qs = (
            EmissionFactor.objects
            .filter(dataset__status=EmissionFactorDataset.Status.ACTIVE)
            .select_related("dataset", "activity_type", "region", "dataset__region")
        )
        if activity_type_ids is not None:
            qs = qs.filter(activity_type_id__in=list(activity_type_ids))
        self._by_type = defaultdict(list)
        for f in qs:
            self._by_type[f.activity_type_id].append(f)

    @staticmethod
    def _region_code(factor):
        if factor.region:
            return factor.region.code
        if factor.dataset.region:
            return factor.dataset.region.code
        return GLOBAL

    def resolve(self, activity_type_id, activity_date=None,
                org_region_code=None, preferred_publisher="", strict=False):
        matches = []
        for f in self._by_type.get(activity_type_id, []):
            ef_from = f.valid_from or f.dataset.valid_from
            ef_to = f.valid_to or f.dataset.valid_to
            if activity_date is not None:
                if ef_from and activity_date < ef_from:
                    continue
                if ef_to and activity_date > ef_to:
                    continue
            region_code = self._region_code(f)
            is_global = region_code == GLOBAL
            if org_region_code and region_code not in (org_region_code, GLOBAL):
                continue
            if strict and is_global and org_region_code:
                continue
            matches.append((f, is_global))

        if not matches:
            return None

        def sort_key(item):
            f, is_global = item
            publisher_rank = 0 if (preferred_publisher and f.dataset.publisher == preferred_publisher) else 1
            region_rank = 1 if is_global else 0  # specific before global
            return (
                publisher_rank,
                region_rank,
                -f.dataset.priority,
                -f.dataset.import_timestamp.timestamp(),
                str(f.id),
            )

        matches.sort(key=sort_key)
        return matches[0][0]
