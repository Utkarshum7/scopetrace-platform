"""
End-to-end: ingestion now produces EmissionCalculations. Includes the flight
class-weighting PARITY test — the DEFRA seat-class multiplier remains applied at
normalization (kept there to avoid splitting activity-data semantics between
legacy and new records), and this test locks the resulting CO2e end-to-end.
"""
import json
import os
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from apps.carbon.models import EmissionCalculation
from apps.core.models import DataSource, Organization
from apps.ingestion.models import EmissionRecord
from apps.ingestion.services.ingestion_service import IngestionService

RS = EmissionCalculation.ResolutionStatus


def _write(content, suffix):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


class IngestionCalculationTests(TestCase):
    def setUp(self):
        call_command("seed_carbon")  # regions, activity types, mappings, DEFRA factors
        self.org = Organization.objects.create(name="Ingest Carbon Org")
        self.sap_ds = DataSource.objects.create(
            organization=self.org, name="SAP", source_type=DataSource.SourceType.SAP_FUEL
        )
        self.travel_ds = DataSource.objects.create(
            organization=self.org, name="Travel", source_type=DataSource.SourceType.CORP_TRAVEL
        )
        self.service = IngestionService()

    def _ingest(self, data_source, content, suffix):
        path = _write(content, suffix)
        try:
            return self.service.ingest(data_source, path, original_filename="f" + suffix)
        finally:
            os.remove(path)

    def test_diesel_ingestion_produces_calculation(self):
        csv = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            "DE01;01.06.2024;DSL;Diesel;1000,00;L;1500.00\n"
        )
        result = self._ingest(self.sap_ds, csv, ".csv")
        record = EmissionRecord.objects.get(batch=result.batch)
        calc = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(calc.resolution_status, RS.CALCULATED)
        # 1000 L x 2.68205 = 2682.05 kg
        self.assertEqual(calc.co2e_kg, Decimal("2682.050000"))
        self.assertEqual(calc.factor_publisher, "DEFRA")
        self.assertEqual(calc.factor_version, "2024")
        self.assertTrue(calc.calculation_trace["steps"])

    def test_flight_class_weighting_parity(self):
        # Business flight, 1000 km physical. Normalizer applies the DEFRA
        # business multiplier (x2.9) -> 2900 km normalized. Factor
        # FLIGHT_SHORT_HAUL = 0.15102 kgCO2e/km.
        # Expected CO2e = 2900 x 0.15102 = 437.958 kg.
        travel = json.dumps([{
            "trip_id": "T1", "travel_mode": "FLIGHT", "origin": "LHR",
            "destination": "JFK", "distance_km": 1000, "travel_date": "2024-06-01",
            "employee_id": "E1", "class": "BUSINESS",
        }])
        result = self._ingest(self.travel_ds, travel, ".json")
        record = EmissionRecord.objects.get(batch=result.batch)
        self.assertEqual(record.normalized_value, Decimal("2900.000000"))
        calc = EmissionCalculation.objects.get(emission_record=record, is_current=True)
        self.assertEqual(calc.resolution_status, RS.CALCULATED)
        self.assertEqual(calc.activity_quantity, Decimal("2900.000000"))
        self.assertEqual(calc.co2e_kg, Decimal("437.958000"))

    def test_every_record_gets_one_current_calculation(self):
        csv = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            "DE01;01.06.2024;DSL;Diesel;1000,00;L;1500.00\n"
            "DE02;01.06.2024;DSL;Diesel;500,00;L;750.00\n"
        )
        result = self._ingest(self.sap_ds, csv, ".csv")
        records = EmissionRecord.objects.filter(batch=result.batch)
        self.assertEqual(records.count(), 2)
        for record in records:
            self.assertEqual(
                EmissionCalculation.objects.filter(emission_record=record, is_current=True).count(),
                1,
            )
