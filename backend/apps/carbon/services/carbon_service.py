"""
CarbonCalculationService — orchestrates the staged pipeline over a batch of
activity inputs and maps results to (unsaved) EmissionCalculation objects.

Pure/stateless with respect to the database: it never writes. Callers
(ingestion, backfill, recalculation) own persistence — this keeps the engine
testable and Celery-ready (Phase 5). Batch resources are preloaded once, so a
1M-record run performs no per-row queries during resolution.
"""
from apps.carbon.models import (
    EmissionCalculation,
    EmissionFactorDataset,
    OrgFactorPolicy,
)
from apps.carbon.precision import to_decimal
from apps.carbon.services.pipeline import (
    DEFAULT_STAGES,
    CalculationContext,
    CalculationResources,
)
from apps.carbon.services.resolution import ActivityTypeResolver, FactorIndex
from apps.carbon.services.units import UnitConverter

ENGINE_VERSION = "1.0"


class CarbonCalculationService:
    def __init__(self, stages=None):
        # Custom stages can be injected (e.g. a future AI stage) without
        # changing the default pipeline.
        self.stages = stages if stages is not None else DEFAULT_STAGES

    def build_resources(self, organization) -> CalculationResources:
        """Preload resolvers/config for a batch belonging to one organization."""
        policy = OrgFactorPolicy.objects.filter(organization=organization).first()
        region_code = None
        preferred_publisher = ""
        strict = False
        if policy:
            preferred_publisher = policy.preferred_publisher or ""
            strict = policy.strict_mode
            if policy.default_region:
                region_code = policy.default_region.code
        return CalculationResources(
            activity_type_resolver=ActivityTypeResolver(),
            factor_index=FactorIndex(),
            unit_converter=UnitConverter(),
            org_region_code=region_code,
            preferred_publisher=preferred_publisher,
            strict_mode=strict,
            engine_version=ENGINE_VERSION,
        )

    def calculate_one(self, activity_input, resources) -> CalculationContext:
        context = CalculationContext(input=activity_input)
        for stage in self.stages:
            try:
                stage.process(context, resources)
            except Exception as exc:
                # Degrade gracefully — a single bad record must never fail the
                # whole batch. Mark it unresolved for later review.
                context.resolution_status = EmissionCalculation.ResolutionStatus.UNRESOLVED_NO_FACTOR
                context.co2e_kg = None
                context.co2e_tonnes = None
                context.notes.append(f"{type(stage).__name__}: {exc}")
                context.halt = True
                break
        return context

    def to_calculation(self, context, organization) -> EmissionCalculation:
        """Map a resolved context to an unsaved EmissionCalculation."""
        inp = context.input
        factor = context.factor
        calc = EmissionCalculation(
            organization=organization,
            emission_record_id=inp.record_id,
            is_current=True,
            activity_type=context.activity_type,
            emission_factor=factor,
            activity_quantity=to_decimal(inp.quantity),
            activity_unit=inp.unit,
            co2e_kg=context.co2e_kg,
            co2e_tonnes=context.co2e_tonnes,
            calculation_trace=context.trace,
            resolution_status=context.resolution_status,
            engine_version=ENGINE_VERSION,
        )
        if factor is not None:
            calc.factor_publisher = factor.dataset.publisher
            calc.factor_version = factor.dataset.version
            calc.factor_value = factor.co2e_per_unit
            calc.factor_unit = factor.unit
        return calc

    def build_calculations(self, activity_inputs, organization):
        """Resolve + compute a batch, returning unsaved EmissionCalculation objects."""
        resources = self.build_resources(organization)
        calculations = []
        for activity_input in activity_inputs:
            context = self.calculate_one(activity_input, resources)
            calculations.append(self.to_calculation(context, organization))
        return calculations
