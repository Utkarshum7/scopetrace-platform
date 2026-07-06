"""
CarbonCalculationService — orchestrates the staged pipeline over a batch of
activity inputs and maps results to (unsaved) EmissionCalculation objects.

Pure/stateless with respect to the database: it never writes. Callers
(ingestion, backfill, recalculation) own persistence — this keeps the engine
testable and Celery-ready (Phase 5). Batch resources are preloaded once, so a
1M-record run performs no per-row queries during resolution.
"""
from django.db import transaction
from django.utils import timezone

from apps.carbon.models import (
    EmissionCalculation,
    EmissionFactorDataset,
    OrgFactorPolicy,
)
from apps.carbon.precision import to_decimal
from apps.carbon.services.inputs import activity_input_from_record
from apps.carbon.services.metrics_cache import bump_calc_version
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
        reporting_date = inp.activity_date
        reporting_month = reporting_date.replace(day=1) if reporting_date else None
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
            # Analytic dimensions (denormalized for the Metrics API)
            scope=inp.scope or "",
            reporting_date=reporting_date,
            reporting_month=reporting_month,
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

    def calculate_for_batch(self, batch) -> list:
        """Compute + persist EmissionCalculations for every EmissionRecord in
        `batch` (Phase 5d) — the calculate stage of the ingest->calculate
        chain's second link (apps.carbon.tasks.calculate_task), and also
        called inline by the synchronous IngestionService.ingest()
        convenience path so direct/test callers see identical end-to-end
        behavior to before the chain existed.

        Re-fetches records from the DB via activity_input_from_record()
        rather than depending on the original parse pass's in-memory Row
        objects — this is what lets it run independently, after ingestion
        has already committed and moved on to a separate task/process.
        apps.carbon already has a hard dependency on apps.ingestion at the
        model level (EmissionCalculation.emission_record FKs to
        ingestion.EmissionRecord) — importing EmissionRecord here is
        consistent with that, not new coupling; kept lazy (function-local)
        purely to avoid a module-load-order dependency between the two
        apps' service layers.

        Owns batch.calculation_status (CALCULATING -> CALCULATED/
        CALCULATION_FAILED) and batch.finished_at (Phase 5d: marks the end
        of the WHOLE chain, not just ingestion) — independent of
        batch.status, which reflects ingestion outcome only. Unresolved
        factors do NOT raise here — CarbonCalculationService.calculate_one()
        already degrades a single bad record to UNRESOLVED rather than
        failing the batch; only a genuine crash (e.g. a DB error during
        bulk_create) reaches this method's except clause.

        Runs in its own transaction, separate from ingestion's — a
        calculation-stage failure must never roll back the already-durably-
        committed ingestion records (that's the whole point of splitting the
        chain; see docs/JOB_LIFECYCLE.md).
        """
        from apps.ingestion.models import EmissionRecord, UploadBatch

        if batch.calculation_status not in UploadBatch.CALCULATION_TERMINAL_STATUSES:
            batch.calculation_status = UploadBatch.CalculationStatus.CALCULATING
            batch.save(update_fields=["calculation_status"])

        try:
            with transaction.atomic():
                records = list(
                    EmissionRecord.objects.filter(batch=batch)
                    .select_related("batch__data_source")
                )

                calculations = []
                if records:
                    inputs = [activity_input_from_record(r) for r in records]
                    calculations = self.build_calculations(inputs, batch.organization)
                    EmissionCalculation.objects.bulk_create(calculations)
                    bump_calc_version(batch.organization_id)

                batch.calculation_status = UploadBatch.CalculationStatus.CALCULATED
                batch.finished_at = timezone.now()
                batch.save(update_fields=["calculation_status", "finished_at"])

            return calculations
        except Exception as exc:
            batch.calculation_status = UploadBatch.CalculationStatus.CALCULATION_FAILED
            batch.error_message = f"Calculation failed: {type(exc).__name__}: {exc}"
            batch.finished_at = timezone.now()
            batch.save(update_fields=["calculation_status", "error_message", "finished_at"])
            raise
