"""
Carbon calculation pipeline.

The calculation is a staged pipeline over a mutable CalculationContext:

    RuleEngine -> FactorResolution -> AIRecommendation(hook) -> Calculation
                                                             -> Optimization(hook)

Stages are independent and composable. Future AI modules register a stage
(e.g. a real AIRecommendationStage or OptimizationStage) WITHOUT modifying the
existing stages. No AI is implemented in Phase 3 — the AI stages are inert
pass-throughs that establish the interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from apps.carbon.precision import (
    kg_to_tonnes,
    quantize_kg,
    quantize_tonnes,
    to_decimal,
)


@dataclass
class ActivityInput:
    """Normalized activity data handed to the engine (source-agnostic)."""
    record_id: str
    organization_id: str
    source_type: str
    quantity: Decimal
    unit: str
    scope: str
    match_keys: list = field(default_factory=list)
    activity_date: date | None = None
    status: str = "DRAFT"


@dataclass
class CalculationResources:
    """Preloaded, per-batch resolvers/config (no per-row queries)."""
    activity_type_resolver: object
    factor_index: object
    unit_converter: object
    org_region_code: str | None = None
    preferred_publisher: str = ""
    strict_mode: bool = False
    engine_version: str = "1.0"


@dataclass
class CalculationContext:
    """Mutable state threaded through the stages."""
    input: ActivityInput
    activity_type: object = None
    factor: object = None
    converted_quantity: Decimal | None = None
    co2e_kg: Decimal | None = None
    co2e_tonnes: Decimal | None = None
    resolution_status: str = "CALCULATED"
    trace: dict = field(default_factory=dict)
    recommendations: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    halt: bool = False


# --- import the model enum lazily to avoid import cycles at module load ---
def _status():
    from apps.carbon.models import EmissionCalculation
    return EmissionCalculation.ResolutionStatus


class CalculationStage(ABC):
    @abstractmethod
    def process(self, context: CalculationContext, resources: CalculationResources) -> None:
        ...


class RuleEngineStage(CalculationStage):
    """Deterministic pre-rules. Failed-validation records are excluded."""

    def process(self, context, resources):
        if context.input.status == "FAILED":
            context.resolution_status = _status().EXCLUDED_FAILED
            context.halt = True


class FactorResolutionStage(CalculationStage):
    """Resolve ActivityType then the applicable EmissionFactor."""

    def process(self, context, resources):
        if context.halt:
            return
        inp = context.input
        activity_type = resources.activity_type_resolver.resolve(inp.source_type, inp.match_keys)
        if activity_type is None:
            context.resolution_status = _status().UNRESOLVED_NO_ACTIVITY_TYPE
            context.halt = True
            return
        context.activity_type = activity_type

        factor = resources.factor_index.resolve(
            activity_type.id,
            activity_date=inp.activity_date,
            org_region_code=resources.org_region_code,
            preferred_publisher=resources.preferred_publisher,
            strict=resources.strict_mode,
        )
        if factor is None:
            context.resolution_status = _status().UNRESOLVED_NO_FACTOR
            context.halt = True
            return
        context.factor = factor


class AIRecommendationStage(CalculationStage):
    """Reserved hook for a future AI recommendation module (factor suggestions,
    anomaly flags). Phase 3 pass-through — establishes the interface only."""

    def process(self, context, resources):
        return  # intentionally inert


class CalculationStage_(CalculationStage):
    """The Decimal math + explainability trace."""

    def process(self, context, resources):
        if context.halt:
            return
        inp = context.input
        factor = context.factor
        quantity = to_decimal(inp.quantity)

        # Convert the activity's base unit to the factor's 'per' unit.
        converted = resources.unit_converter.convert(quantity, inp.unit, factor.unit)
        context.converted_quantity = converted

        # Full-precision multiply; quantize only at the storage boundary.
        co2e_kg_full = converted * to_decimal(factor.co2e_per_unit)
        context.co2e_kg = quantize_kg(co2e_kg_full)
        context.co2e_tonnes = quantize_tonnes(kg_to_tonnes(co2e_kg_full))
        context.resolution_status = _status().CALCULATED

        activity_label = f"{_trim(quantity)} {inp.unit}"
        if context.activity_type:
            activity_label += f" {context.activity_type.name}"
        factor_label = f"{_trim(factor.co2e_per_unit)} kgCO₂e/{factor.unit}"
        source = f"{factor.dataset.publisher} {factor.dataset.version}"
        context.trace = {
            "steps": [
                {"label": "Activity", "value": activity_label},
                {"label": "Factor", "value": factor_label, "source": source},
                {"label": "Formula", "value": f"{_trim(converted)} × {_trim(factor.co2e_per_unit)}"},
                {"label": "Result", "value": f"{_trim(context.co2e_kg)} kgCO₂e"},
                {"label": "Normalized", "value": f"{_trim(context.co2e_tonnes)} tCO₂e"},
            ],
            "activity_quantity": _trim(quantity),
            "activity_unit": inp.unit,
            "converted_quantity": _trim(converted),
            "factor_value": _trim(factor.co2e_per_unit),
            "factor_unit": factor.unit,
            "factor_source": source,
            "co2e_kg": str(context.co2e_kg),
            "co2e_tonnes": str(context.co2e_tonnes),
            "engine_version": resources.engine_version,
        }


class OptimizationStage(CalculationStage):
    """Reserved hook for a future optimization/suggestion module. Inert in Phase 3."""

    def process(self, context, resources):
        return


def _trim(value) -> str:
    """Human-friendly Decimal string (strip trailing zeros, keep at least one dp-less int)."""
    d = to_decimal(value).normalize()
    s = format(d, "f")
    return s


DEFAULT_STAGES = [
    RuleEngineStage(),
    FactorResolutionStage(),
    AIRecommendationStage(),
    CalculationStage_(),
    OptimizationStage(),
]
