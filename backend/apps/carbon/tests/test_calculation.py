import uuid
from datetime import date
from decimal import Decimal

from django.test import TestCase

from apps.carbon.models import EmissionCalculation, UnitConversion
from apps.carbon.services.carbon_service import CarbonCalculationService
from apps.carbon.services.pipeline import ActivityInput
from apps.carbon.tests import factories as f
from apps.core.models import Organization

RS = EmissionCalculation.ResolutionStatus


class CalculationEngineTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Calc Org")
        self.service = CarbonCalculationService()

        # Activity types + mappings
        self.diesel = f.activity_type("DIESEL_STATIONARY", base_unit="L")
        self.elec = f.activity_type("GRID_ELECTRICITY", scope="SCOPE_2", base_unit="kWh")
        f.mapping("SAP_FUEL", self.diesel, match_key="")
        f.mapping("UTILITY_ELECTRICITY", self.elec, match_key="")

        # Factors (DEFRA 2024 window)
        ds = f.dataset(version="2024", valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31))
        f.factor(ds, self.diesel, "2.68", unit="L")
        f.factor(ds, self.elec, "0.20707", unit="kWh")

        # Unit conversion for the MWh -> kWh path
        f.unit_conversion("MWh", "kWh", "1000", UnitConversion.Dimension.ENERGY)

    def _res(self):
        # Build fresh each call so factors added within a test are indexed.
        return self.service.build_resources(self.org)

    def _input(self, source_type, qty, unit, match_keys=None, status="DRAFT"):
        return ActivityInput(
            record_id=str(uuid.uuid4()),
            organization_id=str(self.org.id),
            source_type=source_type,
            quantity=Decimal(qty),
            unit=unit,
            scope="SCOPE_1",
            match_keys=match_keys or [],
            activity_date=date(2024, 6, 1),
            status=status,
        )

    # --- Golden values ---
    def test_golden_diesel(self):
        ctx = self.service.calculate_one(self._input("SAP_FUEL", "1200", "L", ["DSL"]), self._res())
        self.assertEqual(ctx.resolution_status, RS.CALCULATED)
        self.assertEqual(ctx.co2e_kg, Decimal("3216.000000"))
        self.assertEqual(ctx.co2e_tonnes, Decimal("3.216000000"))

    def test_golden_diesel_high_precision_factor(self):
        f.factor(
            f.dataset(version="hp", valid_from=date(2023, 1, 1), valid_to=date(2023, 12, 31)),
            self.diesel, "2.51233", unit="L",
        )
        inp = self._input("SAP_FUEL", "1000", "L")
        inp.activity_date = date(2023, 6, 1)
        ctx = self.service.calculate_one(inp, self._res())
        self.assertEqual(ctx.co2e_kg, Decimal("2512.330000"))

    def test_golden_electricity_with_unit_conversion(self):
        # 2.5 MWh -> 2500 kWh x 0.20707 = 517.675 kg
        ctx = self.service.calculate_one(self._input("UTILITY_ELECTRICITY", "2.5", "MWh"), self._res())
        self.assertEqual(ctx.co2e_kg, Decimal("517.675000"))
        self.assertEqual(ctx.co2e_tonnes, Decimal("0.517675000"))

    def test_precision_no_float_artifacts(self):
        f.factor(
            f.dataset(version="p", valid_from=date(2022, 1, 1), valid_to=date(2022, 12, 31)),
            self.diesel, "3", unit="L",
        )
        inp = self._input("SAP_FUEL", "0.1", "L")
        inp.activity_date = date(2022, 6, 1)
        ctx = self.service.calculate_one(inp, self._res())
        self.assertEqual(ctx.co2e_kg, Decimal("0.300000"))  # not 0.30000000004

    # --- Explainability ---
    def test_explainability_trace(self):
        ctx = self.service.calculate_one(self._input("SAP_FUEL", "1200", "L"), self._res())
        trace = ctx.trace
        labels = [s["label"] for s in trace["steps"]]
        self.assertEqual(labels, ["Activity", "Factor", "Formula", "Result", "Normalized"])
        values = {s["label"]: s["value"] for s in trace["steps"]}
        self.assertEqual(values["Formula"], "1200 × 2.68")
        self.assertEqual(values["Result"], "3216 kgCO₂e")
        self.assertEqual(values["Normalized"], "3.216 tCO₂e")
        self.assertEqual(trace["factor_value"], "2.68")
        self.assertEqual(trace["co2e_kg"], "3216.000000")
        self.assertEqual(trace["steps"][1]["source"], "DEFRA 2024")

    # --- Unresolved / excluded paths ---
    def test_unresolved_no_activity_type(self):
        ctx = self.service.calculate_one(self._input("UNKNOWN", "10", "L"), self._res())
        self.assertEqual(ctx.resolution_status, RS.UNRESOLVED_NO_ACTIVITY_TYPE)
        self.assertIsNone(ctx.co2e_kg)

    def test_unresolved_no_factor(self):
        # ActivityType mapped, but no factor exists for it.
        rail = f.activity_type("RAIL", scope="SCOPE_3", base_unit="km")
        f.mapping("CORP_TRAVEL", rail, match_key="")
        ctx = self.service.calculate_one(self._input("CORP_TRAVEL", "500", "km"), self._res())
        self.assertEqual(ctx.resolution_status, RS.UNRESOLVED_NO_FACTOR)

    def test_failed_record_excluded(self):
        ctx = self.service.calculate_one(self._input("SAP_FUEL", "1200", "L", status="FAILED"), self._res())
        self.assertEqual(ctx.resolution_status, RS.EXCLUDED_FAILED)
        self.assertIsNone(ctx.co2e_kg)

    # --- to_calculation snapshot ---
    def test_to_calculation_snapshots_factor(self):
        ctx = self.service.calculate_one(self._input("SAP_FUEL", "1200", "L"), self._res())
        calc = self.service.to_calculation(ctx, self.org)
        self.assertEqual(calc.factor_publisher, "DEFRA")
        self.assertEqual(calc.factor_version, "2024")
        self.assertEqual(calc.factor_value, Decimal("2.68"))
        self.assertEqual(calc.factor_unit, "L")
        self.assertEqual(calc.activity_quantity, Decimal("1200"))
        self.assertEqual(calc.co2e_kg, Decimal("3216.000000"))
