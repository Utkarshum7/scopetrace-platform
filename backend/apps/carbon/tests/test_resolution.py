from datetime import date

from django.test import TestCase

from apps.carbon.models import EmissionFactorDataset, Publisher
from apps.carbon.services.resolution import ActivityTypeResolver, FactorIndex
from apps.carbon.tests import factories as f


class ActivityTypeResolverTests(TestCase):
    def setUp(self):
        self.diesel = f.activity_type("DIESEL_STATIONARY")
        self.gas = f.activity_type("NATURAL_GAS")
        f.mapping("SAP_FUEL", self.diesel, match_key="")          # default
        f.mapping("SAP_FUEL", self.gas, match_key="GAS")          # specific
        self.resolver = ActivityTypeResolver()

    def test_default_mapping(self):
        self.assertEqual(self.resolver.resolve("SAP_FUEL", []), self.diesel)

    def test_specific_match_key_wins(self):
        self.assertEqual(self.resolver.resolve("SAP_FUEL", ["GAS"]), self.gas)

    def test_match_key_is_case_insensitive(self):
        self.assertEqual(self.resolver.resolve("SAP_FUEL", ["gas"]), self.gas)

    def test_unknown_source_returns_none(self):
        self.assertIsNone(self.resolver.resolve("UNKNOWN_SRC", ["X"]))


class FactorResolutionTests(TestCase):
    def setUp(self):
        self.diesel = f.activity_type("DIESEL_STATIONARY")
        self.glob = f.region("GLOBAL")
        self.gb = f.region("GB", "United Kingdom")

    def test_basic_resolution_within_window(self):
        ds = f.dataset(version="2024", valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
        fac = f.factor(ds, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertEqual(idx.resolve(self.diesel.id, date(2024, 6, 1)), fac)

    def test_effective_dating_excludes_out_of_window(self):
        ds = f.dataset(version="2024", valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
        f.factor(ds, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertIsNone(idx.resolve(self.diesel.id, date(2025, 6, 1)))

    def test_effective_dating_picks_correct_year(self):
        ds24 = f.dataset(version="2024", valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
        ds25 = f.dataset(publisher=Publisher.DEFRA, version="2025",
                         valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
        f24 = f.factor(ds24, self.diesel, "2.68")
        f25 = f.factor(ds25, self.diesel, "2.51")
        idx = FactorIndex()
        self.assertEqual(idx.resolve(self.diesel.id, date(2024, 6, 1)), f24)
        self.assertEqual(idx.resolve(self.diesel.id, date(2025, 6, 1)), f25)

    def test_region_specificity_prefers_specific_over_global(self):
        ds_global = f.dataset(version="g", region_obj=self.glob)
        ds_gb = f.dataset(publisher=Publisher.DEFRA, version="gb", region_obj=self.gb)
        f.factor(ds_global, self.diesel, "2.00")
        f_gb = f.factor(ds_gb, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertEqual(idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="GB"), f_gb)

    def test_global_used_when_no_specific(self):
        ds_global = f.dataset(version="g", region_obj=self.glob)
        f_global = f.factor(ds_global, self.diesel, "2.00")
        idx = FactorIndex()
        self.assertEqual(idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="GB"), f_global)

    def test_strict_mode_excludes_global(self):
        ds_global = f.dataset(version="g", region_obj=self.glob)
        f.factor(ds_global, self.diesel, "2.00")
        idx = FactorIndex()
        self.assertIsNone(
            idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="GB", strict=True)
        )

    def test_publisher_preference(self):
        ds_defra = f.dataset(publisher=Publisher.DEFRA, version="d", region_obj=self.glob)
        ds_epa = f.dataset(publisher=Publisher.EPA, version="e", region_obj=self.glob)
        f_defra = f.factor(ds_defra, self.diesel, "2.68")
        f.factor(ds_epa, self.diesel, "2.70")
        idx = FactorIndex()
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), preferred_publisher=Publisher.EPA).dataset.publisher,
            Publisher.EPA,
        )
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), preferred_publisher=Publisher.DEFRA),
            f_defra,
        )

    def test_priority_breaks_ties(self):
        ds_low = f.dataset(version="low", region_obj=self.glob, priority=50)
        ds_high = f.dataset(publisher=Publisher.DEFRA, version="high", region_obj=self.glob, priority=200)
        f.factor(ds_low, self.diesel, "2.00")
        f_high = f.factor(ds_high, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertEqual(idx.resolve(self.diesel.id, date(2024, 6, 1)), f_high)

    def test_only_active_datasets_considered(self):
        ds_draft = f.dataset(version="draft", status=EmissionFactorDataset.Status.DRAFT)
        f.factor(ds_draft, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertIsNone(idx.resolve(self.diesel.id, date(2024, 6, 1)))


class RegionHierarchyResolutionTests(TestCase):
    """Phase 7.5 (H4-12): a factor scoped to an ANCESTOR region (e.g. "EU")
    must be matchable for an org in a descendant region (e.g. "DE") --
    previously Region.parent was declared on the model but never consulted
    during resolution, so only an exact region-code match or GLOBAL ever
    matched; anything in between (continent/country-group factors) was
    silently invisible no matter how the data was modeled."""

    def setUp(self):
        self.diesel = f.activity_type("DIESEL_STATIONARY")
        self.glob = f.region("GLOBAL")
        self.eu = f.region("EU", "European Union")
        self.de = f.region("DE", "Germany")
        self.de.parent = self.eu
        self.de.save()
        self.eu.parent = self.glob
        self.eu.save()

    def test_ancestor_region_factor_matches_a_descendant_org(self):
        ds_eu = f.dataset(version="eu", region_obj=self.eu)
        f_eu = f.factor(ds_eu, self.diesel, "2.30")
        idx = FactorIndex()
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="DE"), f_eu,
        )

    def test_exact_region_still_beats_ancestor_region(self):
        ds_eu = f.dataset(version="eu", region_obj=self.eu)
        ds_de = f.dataset(publisher=Publisher.DEFRA, version="de", region_obj=self.de)
        f.factor(ds_eu, self.diesel, "2.30")
        f_de = f.factor(ds_de, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="DE"), f_de,
        )

    def test_ancestor_region_beats_global(self):
        ds_global = f.dataset(version="g", region_obj=self.glob)
        ds_eu = f.dataset(publisher=Publisher.DEFRA, version="eu", region_obj=self.eu)
        f.factor(ds_global, self.diesel, "2.00")
        f_eu = f.factor(ds_eu, self.diesel, "2.30")
        idx = FactorIndex()
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="DE"), f_eu,
        )

    def test_closer_ancestor_beats_farther_ancestor(self):
        # DE -> EU -> GLOBAL: a factor scoped to EU (rank 1) must beat one
        # scoped to GLOBAL (rank 2) for an org in DE.
        ds_global = f.dataset(version="g", region_obj=self.glob)
        ds_eu = f.dataset(publisher=Publisher.DEFRA, version="eu", region_obj=self.eu)
        f_global = f.factor(ds_global, self.diesel, "2.00")
        f_eu = f.factor(ds_eu, self.diesel, "2.30")
        idx = FactorIndex()
        result = idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="DE")
        self.assertEqual(result, f_eu)
        self.assertNotEqual(result, f_global)

    def test_unrelated_region_never_matches(self):
        # A factor scoped to GB (no ancestor relationship to DE) must never
        # match a DE org -- confirms the ancestor-chain fix doesn't loosen
        # matching into "any region is fine now".
        gb = f.region("GB", "United Kingdom")
        ds_gb = f.dataset(version="gb", region_obj=gb)
        f.factor(ds_gb, self.diesel, "2.68")
        idx = FactorIndex()
        self.assertIsNone(idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="DE"))

    def test_unknown_org_region_code_falls_back_to_exact_or_global_only(self):
        # A stale/typo'd org region code (no matching Region row) must
        # behave exactly as before this fix: only an exact string match or
        # GLOBAL, never silently matching an unrelated ancestor chain.
        ds_global = f.dataset(version="g", region_obj=self.glob)
        f_global = f.factor(ds_global, self.diesel, "2.00")
        idx = FactorIndex()
        self.assertEqual(
            idx.resolve(self.diesel.id, date(2024, 6, 1), org_region_code="ZZ-NOPE"), f_global,
        )
