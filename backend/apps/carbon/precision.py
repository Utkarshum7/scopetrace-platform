"""
Decimal precision helpers for the carbon engine.

All carbon arithmetic uses Python `Decimal` (never float). Full precision is
carried through the convert x multiply chain; quantization (ROUND_HALF_UP) is
applied ONLY at the storage boundary via the helpers below. Storage scales:

    EmissionFactor.co2e_per_unit : Decimal(30, 12)  -> FACTOR_QUANT
    EmissionCalculation.co2e_kg  : Decimal(20, 6)   -> KG_QUANT
    EmissionCalculation.co2e_tonnes : Decimal(20, 9) -> TONNE_QUANT

The default decimal context (28 significant digits) comfortably covers our
magnitudes, so the global context is left untouched.
"""
from decimal import Decimal, ROUND_HALF_UP

FACTOR_QUANT = Decimal("0.000000000001")  # 12 dp
KG_QUANT = Decimal("0.000001")            # 6 dp
TONNE_QUANT = Decimal("0.000000001")      # 9 dp
KG_PER_TONNE = Decimal("1000")


def to_decimal(value) -> Decimal:
    """Coerce ints/floats/strings/Decimals to Decimal without float artifacts."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def quantize_factor(value: Decimal) -> Decimal:
    return to_decimal(value).quantize(FACTOR_QUANT, rounding=ROUND_HALF_UP)


def quantize_kg(value: Decimal) -> Decimal:
    return to_decimal(value).quantize(KG_QUANT, rounding=ROUND_HALF_UP)


def quantize_tonnes(value: Decimal) -> Decimal:
    return to_decimal(value).quantize(TONNE_QUANT, rounding=ROUND_HALF_UP)


def kg_to_tonnes(kg: Decimal) -> Decimal:
    """Convert kilograms to tonnes at full precision (quantize separately)."""
    return to_decimal(kg) / KG_PER_TONNE
