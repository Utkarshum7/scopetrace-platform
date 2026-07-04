import os
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.carbon.models import EmissionFactor, EmissionFactorDataset
from apps.carbon.services.importers import CsvFactorImporter, FactorImportError
from apps.carbon.tests import factories as f

CSV_HEADER = "activity_type,unit,co2e_per_unit,region,valid_from,valid_to,methodology,source_ref\n"
CSV_V2024 = CSV_HEADER + "DIESEL_STATIONARY,L,2.68205,GB,2024-01-01,2024-12-31,DEFRA,ref\n"
CSV_V2025 = CSV_HEADER + "DIESEL_STATIONARY,L,2.51000,GB,2025-01-01,2025-12-31,DEFRA,ref\n"


def _write(content):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class ImporterParseTests(TestCase):
    def test_parses_valid_rows(self):
        rows = CsvFactorImporter().parse(CSV_V2024)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].activity_type_code, "DIESEL_STATIONARY")
        self.assertEqual(str(rows[0].co2e_per_unit), "2.68205")

    def test_rejects_non_positive(self):
        bad = CSV_HEADER + "DIESEL_STATIONARY,L,0,GB,2024-01-01,2024-12-31,,\n"
        with self.assertRaises(FactorImportError):
            CsvFactorImporter().parse(bad)

    def test_rejects_invalid_number(self):
        bad = CSV_HEADER + "DIESEL_STATIONARY,L,abc,GB,2024-01-01,2024-12-31,,\n"
        with self.assertRaises(FactorImportError):
            CsvFactorImporter().parse(bad)


class ImportCommandTests(TestCase):
    def setUp(self):
        self.diesel = f.activity_type("DIESEL_STATIONARY")

    def _import(self, content, version, **extra):
        path = _write(content)
        try:
            call_command(
                "import_emission_factors", file=path, publisher="DEFRA", dataset_version=version,
                region="GB", valid_from="2024-01-01", valid_to="2024-12-31", **extra,
            )
        finally:
            os.remove(path)

    def test_import_creates_dataset_and_factors(self):
        self._import(CSV_V2024, "2024", activate=True)
        ds = EmissionFactorDataset.objects.get(publisher="DEFRA", version="2024")
        self.assertEqual(ds.status, EmissionFactorDataset.Status.ACTIVE)
        self.assertEqual(ds.factors.count(), 1)
        self.assertTrue(ds.checksum)
        self.assertEqual(ds.source_filename[-4:], ".csv")

    def test_import_is_idempotent_on_same_checksum(self):
        self._import(CSV_V2024, "2024", activate=True)
        self._import(CSV_V2024, "2024", activate=True)  # same content -> skipped
        self.assertEqual(EmissionFactorDataset.objects.filter(version="2024").count(), 1)
        self.assertEqual(EmissionFactor.objects.count(), 1)

    def test_differing_checksum_same_version_is_rejected(self):
        self._import(CSV_V2024, "2024", activate=True)
        altered = CSV_HEADER + "DIESEL_STATIONARY,L,9.99999,GB,2024-01-01,2024-12-31,,\n"
        with self.assertRaises(CommandError):
            self._import(altered, "2024")

    def test_activation_supersedes_prior_version(self):
        self._import(CSV_V2024, "2024", activate=True)
        self._import(CSV_V2025, "2025", activate=True)
        v2024 = EmissionFactorDataset.objects.get(version="2024")
        v2025 = EmissionFactorDataset.objects.get(version="2025")
        self.assertEqual(v2024.status, EmissionFactorDataset.Status.SUPERSEDED)
        self.assertEqual(v2025.status, EmissionFactorDataset.Status.ACTIVE)

    def test_unknown_activity_type_errors(self):
        bad = CSV_HEADER + "UNKNOWN_TYPE,L,1.0,GB,2024-01-01,2024-12-31,,\n"
        with self.assertRaises(CommandError):
            self._import(bad, "x")
        # Nothing persisted (rolled back)
        self.assertEqual(EmissionFactorDataset.objects.count(), 0)

    def test_dry_run_persists_nothing(self):
        self._import(CSV_V2024, "2024", activate=True, dry_run=True)
        self.assertEqual(EmissionFactorDataset.objects.count(), 0)
        self.assertEqual(EmissionFactor.objects.count(), 0)


class SeedCarbonCommandTests(TestCase):
    def test_seed_is_idempotent_and_activates_defra(self):
        call_command("seed_carbon")
        call_command("seed_carbon")  # second run must not duplicate
        active = EmissionFactorDataset.objects.filter(
            publisher="DEFRA", status=EmissionFactorDataset.Status.ACTIVE
        )
        self.assertEqual(active.count(), 1)
        self.assertGreaterEqual(active.first().factors.count(), 8)
