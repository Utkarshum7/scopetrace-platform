"""Milestone 4a: the analytic dimensions (scope / reporting_date / reporting_month)
are populated by both the ingestion pipeline and the backfill command."""
import os
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord, UploadBatch
from apps.ingestion.services.ingestion_service import IngestionService


class ReportingDimensionTests(TestCase):
    def setUp(self):
        call_command("seed_carbon")
        self.org = Organization.objects.create(name="Dim Org")
        self.ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )

    def test_ingestion_populates_dimensions(self):
        csv = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            "DE01;15.06.2024;DSL;Diesel;1000,00;L;1500.00\n"
        )
        fd, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(csv)
        try:
            result = IngestionService().ingest(self.ds, path, original_filename="a.csv")
        finally:
            os.remove(path)
        record = EmissionRecord.objects.get(batch=result.batch)
        calc = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(calc.scope, "SCOPE_1")
        self.assertEqual(str(calc.reporting_date), "2024-06-15")
        self.assertEqual(str(calc.reporting_month), "2024-06-01")

    def test_backfill_populates_dimensions(self):
        batch = UploadBatch.objects.create(
            organization=self.org, data_source=self.ds, file_name="legacy.csv"
        )
        record = EmissionRecord.objects.create(
            organization=self.org, batch=batch, row_index=1,
            raw_data_payload={"Buchungsdatum": "10.06.2024", "Material": "DSL"},
            status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=Decimal("500"), normalized_unit="L", scope_category="SCOPE_1",
        )
        call_command("backfill_calculations")
        calc = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(calc.scope, "SCOPE_1")
        self.assertEqual(str(calc.reporting_date), "2024-06-10")
        self.assertEqual(str(calc.reporting_month), "2024-06-01")
