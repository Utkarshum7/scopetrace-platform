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
    Region,
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
        # Phase 7.5 (H4-12): memoized per-org-region-code ancestor chains
        # (e.g. "DE" -> ["DE", "EU", "GLOBAL"]) so Region.parent hierarchy
        # is actually usable in resolution -- see _region_rank_chain's
        # docstring. Memoized because org_region_code is the SAME value for
        # every record in a batch (see CarbonCalculationService.
        # build_resources, which resolves it once per organization), so
        # this still costs at most one query per batch, preserving this
        # class's stated "no per-row query" design goal.
        self._chain_cache = {}

    @staticmethod
    def _region_code(factor):
        if factor.region:
            return factor.region.code
        if factor.dataset.region:
            return factor.dataset.region.code
        return GLOBAL

    def _region_rank_chain(self, org_region_code):
        """The ordered list of region codes acceptable for `org_region_code`,
        most-specific first: itself, then each ancestor via Region.parent,
        then GLOBAL. A factor's rank in this list IS its specificity rank --
        0 (exact org region) beats 1 (immediate parent, e.g. a country
        matching a factor scoped to its continent) beats 2 (grandparent),
        etc., with GLOBAL always last. A region code with no matching Region
        row (a stale/typo'd code) or no parent falls back to
        [org_region_code, GLOBAL] -- identical to this class's pre-7.5
        behavior, so exact-match and GLOBAL-fallback resolution for every
        already-passing case (see test_resolution.py) is unchanged; this
        only ADDS the ability to match an ancestor in between.
        """
        if org_region_code not in self._chain_cache:
            chain = [org_region_code]
            seen = {org_region_code}
            current = Region.objects.filter(code=org_region_code).select_related("parent").first()
            while current is not None and current.parent is not None:
                parent_code = current.parent.code
                if parent_code in seen:  # defensive: never loop on a data cycle
                    break
                chain.append(parent_code)
                seen.add(parent_code)
                current = current.parent
            if GLOBAL not in seen:
                chain.append(GLOBAL)
            self._chain_cache[org_region_code] = chain
        return self._chain_cache[org_region_code]

    def resolve(self, activity_type_id, activity_date=None,
                org_region_code=None, preferred_publisher="", strict=False):
        chain = self._region_rank_chain(org_region_code) if org_region_code else None

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
            if chain is not None:
                # GLOBAL is always appended to the chain (see
                # _region_rank_chain), so this excludes only a factor
                # scoped to a region genuinely unrelated to org_region_code.
                if region_code not in chain:
                    continue
                region_rank = chain.index(region_code)
            else:
                region_rank = 1 if is_global else 0
            if strict and is_global and org_region_code:
                continue
            matches.append((f, region_rank))

        if not matches:
            return None

        def sort_key(item):
            f, region_rank = item
            publisher_rank = 0 if (preferred_publisher and f.dataset.publisher == preferred_publisher) else 1
            return (
                publisher_rank,
                region_rank,
                -f.dataset.priority,
                -f.dataset.import_timestamp.timestamp(),
                str(f.id),
            )

        matches.sort(key=sort_key)
        return matches[0][0]
