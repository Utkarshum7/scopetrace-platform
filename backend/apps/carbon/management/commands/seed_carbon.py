"""
Seed carbon reference data (regions, activity types, unit conversions, activity
mappings) and import + activate the bundled illustrative DEFRA 2024 factor
subset. Idempotent — safe to run on every deploy.
"""
from pathlib import Path

from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.carbon.models import (
    ActivityMapping,
    ActivityType,
    Region,
    Scope,
    UnitConversion,
)
from apps.core.models import DataSource

ACTIVITY_TYPES = [
    ("DIESEL_STATIONARY", "Diesel (stationary combustion)", Scope.SCOPE_1, "L"),
    ("NATURAL_GAS", "Natural gas", Scope.SCOPE_1, "L"),
    ("GRID_ELECTRICITY", "Grid electricity", Scope.SCOPE_2, "kWh"),
    ("FLIGHT_DOMESTIC", "Flight (domestic)", Scope.SCOPE_3, "km"),
    ("FLIGHT_SHORT_HAUL", "Flight (short-haul)", Scope.SCOPE_3, "km"),
    ("FLIGHT_LONG_HAUL", "Flight (long-haul)", Scope.SCOPE_3, "km"),
    ("RAIL_NATIONAL", "National rail", Scope.SCOPE_3, "km"),
    ("CAR_RENTAL", "Car (rental)", Scope.SCOPE_3, "km"),
]

UNIT_CONVERSIONS = [
    ("MWh", "kWh", "1000", UnitConversion.Dimension.ENERGY),
    ("t", "kg", "1000", UnitConversion.Dimension.MASS),
    ("GAL", "L", "3.785411784", UnitConversion.Dimension.VOLUME),
    ("M3", "L", "1000", UnitConversion.Dimension.VOLUME),
]

MAPPINGS = [
    (DataSource.SourceType.SAP_FUEL, "", "DIESEL_STATIONARY"),
    (DataSource.SourceType.SAP_FUEL, "GAS", "NATURAL_GAS"),
    (DataSource.SourceType.UTILITY_ELECTRICITY, "", "GRID_ELECTRICITY"),
    (DataSource.SourceType.CORP_TRAVEL, "FLIGHT", "FLIGHT_SHORT_HAUL"),
    (DataSource.SourceType.CORP_TRAVEL, "RAIL", "RAIL_NATIONAL"),
    (DataSource.SourceType.CORP_TRAVEL, "CAR_RENTAL", "CAR_RENTAL"),
    (DataSource.SourceType.CORP_TRAVEL, "TAXI", "CAR_RENTAL"),
]


class Command(BaseCommand):
    help = "Seed carbon reference data and import the bundled DEFRA 2024 factor subset (idempotent)."

    def handle(self, *args, **options):
        Region.objects.get_or_create(code="GLOBAL", defaults={"name": "Global"})
        Region.objects.get_or_create(code="GB", defaults={"name": "United Kingdom"})

        types = {}
        for code, name, scope, unit in ACTIVITY_TYPES:
            at, _ = ActivityType.objects.get_or_create(
                code=code, defaults={"name": name, "default_scope": scope, "base_unit": unit}
            )
            types[code] = at
        self.stdout.write(f"Activity types: {ActivityType.objects.count()}")

        for from_unit, to_unit, factor, dimension in UNIT_CONVERSIONS:
            UnitConversion.objects.get_or_create(
                from_unit=from_unit, to_unit=to_unit,
                defaults={"factor": Decimal(factor), "dimension": dimension},
            )

        for source_type, match_key, at_code in MAPPINGS:
            ActivityMapping.objects.get_or_create(
                data_source_type=source_type, match_key=match_key,
                defaults={"activity_type": types[at_code]},
            )
        self.stdout.write(f"Activity mappings: {ActivityMapping.objects.count()}")

        seed = Path(__file__).resolve().parents[2] / "seed_data" / "defra_2024_seed.csv"
        call_command(
            "import_emission_factors",
            file=str(seed),
            publisher="DEFRA",
            dataset_version="2024",
            name="DEFRA 2024 UK GHG Conversion Factors (illustrative subset)",
            region="GB",
            valid_from="2024-01-01",
            valid_to="2024-12-31",
            publication_date="2024-06-01",
            source_url="https://www.gov.uk/government/collections/government-conversion-factors-for-company-reporting",
            notes="Illustrative seed subset shipped with ScopeTrace; replace with the full official dataset in production.",
            activate=True,
        )
        self.stdout.write(self.style.SUCCESS("seed_carbon complete."))
