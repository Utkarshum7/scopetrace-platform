"""Test helpers for building carbon reference data."""
from datetime import date
from decimal import Decimal

from apps.carbon.models import (
    ActivityMapping,
    ActivityType,
    EmissionFactor,
    EmissionFactorDataset,
    Publisher,
    Region,
    Scope,
    UnitConversion,
)

ACTIVE = EmissionFactorDataset.Status.ACTIVE


def region(code="GLOBAL", name=None):
    return Region.objects.get_or_create(code=code, defaults={"name": name or code})[0]


def activity_type(code="DIESEL_STATIONARY", scope=Scope.SCOPE_1, base_unit="L", name=None):
    return ActivityType.objects.get_or_create(
        code=code,
        defaults={"name": name or code, "default_scope": scope, "base_unit": base_unit},
    )[0]


def dataset(publisher=Publisher.DEFRA, version="2024", status=ACTIVE,
            valid_from=date(2024, 1, 1), valid_to=date(2024, 12, 31),
            region_obj=None, priority=100):
    return EmissionFactorDataset.objects.create(
        publisher=publisher, name=f"{publisher} {version}", version=version,
        status=status, valid_from=valid_from, valid_to=valid_to,
        region=region_obj, priority=priority,
    )


def factor(dataset_obj, activity_type_obj, value="2.68", unit="L",
           region_obj=None, valid_from=None, valid_to=None):
    return EmissionFactor.objects.create(
        dataset=dataset_obj, activity_type=activity_type_obj, unit=unit,
        co2e_per_unit=Decimal(value), region=region_obj,
        valid_from=valid_from, valid_to=valid_to,
    )


def unit_conversion(from_unit, to_unit, factor_value, dimension):
    return UnitConversion.objects.create(
        from_unit=from_unit, to_unit=to_unit,
        factor=Decimal(factor_value), dimension=dimension,
    )


def mapping(source_type, activity_type_obj, match_key=""):
    return ActivityMapping.objects.create(
        data_source_type=source_type, match_key=match_key, activity_type=activity_type_obj
    )
