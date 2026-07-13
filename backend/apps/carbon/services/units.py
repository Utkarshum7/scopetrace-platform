"""Deterministic, dimension-checked unit conversion (Decimal)."""
from decimal import Decimal

from apps.carbon.models import UnitConversion
from apps.carbon.precision import to_decimal


class UnitConversionError(Exception):
    """Raised when no conversion path exists between two units."""


class UnitConverter:
    """
    Converts a Decimal quantity between units using the UnitConversion table.

    Identity when from == to. Supports the declared direction and its inverse.
    Loads the table once (safe for per-batch reuse) — no per-row query.
    """

    def __init__(self):
        self._table = None

    def _load(self):
        if self._table is None:
            self._table = {
                (uc.from_unit, uc.to_unit): uc.factor
                for uc in UnitConversion.objects.all()
            }
        return self._table

    def convert(self, quantity, from_unit: str, to_unit: str) -> Decimal:
        q = to_decimal(quantity)
        if from_unit == to_unit:
            return q
        table = self._load()
        factor = table.get((from_unit, to_unit))
        if factor is not None:
            return q * factor
        inverse = table.get((to_unit, from_unit))
        if inverse is not None and inverse != 0:
            return q / inverse
        raise UnitConversionError(
            f"No unit conversion from '{from_unit}' to '{to_unit}'."
        )
