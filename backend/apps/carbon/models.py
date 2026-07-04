import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import DataSource, Organization


class Scope(models.TextChoices):
    SCOPE_1 = "SCOPE_1", "Scope 1 (Direct)"
    SCOPE_2 = "SCOPE_2", "Scope 2 (Indirect Electricity)"
    SCOPE_3 = "SCOPE_3", "Scope 3 (Other Indirect)"


class Publisher(models.TextChoices):
    DEFRA = "DEFRA", "DEFRA / UK Gov GHG Conversion Factors"
    EPA = "EPA", "US EPA"
    IPCC = "IPCC", "IPCC"
    COUNTRY = "COUNTRY", "Country-specific"
    CUSTOM = "CUSTOM", "Custom / Organization-provided"


# ---------------------------------------------------------------------------
# Reference data (GLOBAL — shared across tenants, no organization FK)
# ---------------------------------------------------------------------------
class Region(models.Model):
    """Resolution geography. `GLOBAL` is the universal fallback."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=16, unique=True, help_text="ISO-3166 / GLOBAL / EU")
    name = models.CharField(max_length=128)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class GwpSet(models.Model):
    """Global Warming Potential set (e.g. AR5, AR6). Reserved seam for future
    per-gas CO2e computation — declared but unused in Phase 3."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=32, unique=True)
    gwp_co2 = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    gwp_ch4 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    gwp_n2o = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name


class ActivityType(models.Model):
    """Controlled vocabulary decoupling parsers/sources from emission factors.
    The stable join key between activity data and factors."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True, help_text="e.g. DIESEL_STATIONARY")
    name = models.CharField(max_length=128)
    default_scope = models.CharField(max_length=20, choices=Scope.choices)
    base_unit = models.CharField(max_length=32, help_text="Canonical activity unit (L, kWh, km)")
    gas_basis = models.CharField(max_length=16, default="CO2E")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["code"]
        indexes = [models.Index(fields=["default_scope"], name="ix_acttype_scope")]

    def __str__(self):
        return self.code


class EmissionFactorDataset(models.Model):
    """
    The versioning + provenance unit. Immutable once ACTIVE — corrections are new
    versions, never edits. Permanently records where the data came from and who
    imported it.
    """
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"
        SUPERSEDED = "SUPERSEDED", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    publisher = models.CharField(max_length=16, choices=Publisher.choices)
    name = models.CharField(max_length=255)
    version = models.CharField(max_length=64, help_text="Publisher version, e.g. 2024.1")
    region = models.ForeignKey(
        Region, null=True, blank=True, on_delete=models.PROTECT, related_name="datasets"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    valid_from = models.DateField(help_text="Start of the effective window for this dataset's factors")
    valid_to = models.DateField(null=True, blank=True)
    priority = models.IntegerField(default=100, help_text="Resolution tie-breaker (higher wins)")

    # Provenance (permanent)
    publication_date = models.DateField(null=True, blank=True, help_text="When the publisher released it")
    import_timestamp = models.DateTimeField(auto_now_add=True, help_text="When ScopeTrace imported it")
    checksum = models.CharField(max_length=64, blank=True, help_text="sha256 of the imported source")
    source_filename = models.CharField(max_length=255, blank=True)
    source_url = models.URLField(blank=True)
    imported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="imported_datasets",
    )
    import_notes = models.TextField(blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Emission Factor Dataset"
        ordering = ["-import_timestamp"]
        constraints = [
            models.UniqueConstraint(
                fields=["publisher", "version", "region"],
                name="unique_dataset_publisher_version_region",
            )
        ]
        indexes = [
            models.Index(fields=["publisher", "status"], name="ix_dataset_pub_status"),
            models.Index(fields=["status", "valid_from"], name="ix_dataset_status_from"),
        ]

    def __str__(self):
        return f"{self.publisher} {self.version} ({self.status})"

    def clean(self):
        super().clean()
        # Immutable once ACTIVE: only the status may change (e.g. -> SUPERSEDED).
        if self.pk:
            try:
                original = EmissionFactorDataset.objects.get(pk=self.pk)
            except EmissionFactorDataset.DoesNotExist:
                return
            if original.status == self.Status.ACTIVE:
                protected = (
                    "publisher", "name", "version", "region_id", "valid_from",
                    "valid_to", "checksum", "source_filename", "source_url",
                    "publication_date", "priority",
                )
                for field in protected:
                    if getattr(original, field) != getattr(self, field):
                        raise ValidationError(
                            f"Dataset is ACTIVE and immutable; '{field}' cannot be changed. "
                            "Publish a new version instead."
                        )


class EmissionFactor(models.Model):
    """A single emission factor value belonging to a dataset."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        EmissionFactorDataset, on_delete=models.CASCADE, related_name="factors"
    )
    activity_type = models.ForeignKey(
        ActivityType, on_delete=models.PROTECT, related_name="factors"
    )
    region = models.ForeignKey(
        Region, null=True, blank=True, on_delete=models.PROTECT, related_name="factors",
        help_text="Region override; null inherits the dataset/global scope",
    )
    unit = models.CharField(max_length=32, help_text="The 'per' unit the factor is expressed in")
    co2e_per_unit = models.DecimalField(max_digits=30, decimal_places=12)

    # Per-gas seam (nullable, unused in Phase 3)
    co2_per_unit = models.DecimalField(max_digits=30, decimal_places=12, null=True, blank=True)
    ch4_per_unit = models.DecimalField(max_digits=30, decimal_places=12, null=True, blank=True)
    n2o_per_unit = models.DecimalField(max_digits=30, decimal_places=12, null=True, blank=True)
    gwp_set = models.ForeignKey(
        GwpSet, null=True, blank=True, on_delete=models.SET_NULL, related_name="factors"
    )

    valid_from = models.DateField(null=True, blank=True, help_text="Overrides dataset window if set")
    valid_to = models.DateField(null=True, blank=True)
    methodology = models.TextField(blank=True)
    uncertainty_pct = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    source_ref = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Emission Factor"
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "activity_type", "unit", "region", "valid_from"],
                name="unique_factor_scope",
            )
        ]
        indexes = [
            models.Index(fields=["activity_type", "region"], name="ix_factor_type_region"),
            models.Index(fields=["dataset"], name="ix_factor_dataset"),
            models.Index(fields=["activity_type", "valid_from", "valid_to"], name="ix_factor_effective"),
        ]

    def __str__(self):
        return f"{self.activity_type.code}: {self.co2e_per_unit} kgCO2e/{self.unit}"

    @property
    def effective_from(self):
        return self.valid_from or self.dataset.valid_from

    @property
    def effective_to(self):
        return self.valid_to or self.dataset.valid_to


class UnitConversion(models.Model):
    """Deterministic, dimension-checked unit conversion factors."""
    class Dimension(models.TextChoices):
        VOLUME = "VOLUME", "Volume"
        ENERGY = "ENERGY", "Energy"
        DISTANCE = "DISTANCE", "Distance"
        MASS = "MASS", "Mass"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_unit = models.CharField(max_length=32)
    to_unit = models.CharField(max_length=32)
    dimension = models.CharField(max_length=16, choices=Dimension.choices)
    factor = models.DecimalField(max_digits=30, decimal_places=12, help_text="1 from_unit = factor to_unit")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["from_unit", "to_unit"], name="unique_unit_conversion")
        ]

    def __str__(self):
        return f"{self.from_unit}->{self.to_unit} x{self.factor}"


class ActivityMapping(models.Model):
    """Resolves a source row to an ActivityType. `match_key` optionally narrows
    by a raw attribute (e.g. SAP material code, travel mode)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    data_source_type = models.CharField(max_length=50, choices=DataSource.SourceType.choices)
    match_key = models.CharField(
        max_length=64, blank=True, help_text="Optional attribute value; blank = default for the source type"
    )
    activity_type = models.ForeignKey(ActivityType, on_delete=models.PROTECT, related_name="mappings")
    region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.SET_NULL)
    priority = models.IntegerField(default=100)

    class Meta:
        verbose_name = "Activity Mapping"
        constraints = [
            models.UniqueConstraint(
                fields=["data_source_type", "match_key"], name="unique_activity_mapping"
            )
        ]
        indexes = [models.Index(fields=["data_source_type", "match_key"], name="ix_mapping_lookup")]

    def __str__(self):
        return f"{self.data_source_type}/{self.match_key or '*'} -> {self.activity_type.code}"


# ---------------------------------------------------------------------------
# Tenant-scoped configuration + results
# ---------------------------------------------------------------------------
class OrgFactorPolicy(models.Model):
    """Per-organization factor preferences (which publisher/region to prefer)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="factor_policy"
    )
    preferred_publisher = models.CharField(
        max_length=16, choices=Publisher.choices, blank=True
    )
    default_region = models.ForeignKey(Region, null=True, blank=True, on_delete=models.SET_NULL)
    strict_mode = models.BooleanField(
        default=False, help_text="If true, do not fall back to GLOBAL factors."
    )

    def __str__(self):
        return f"Policy({self.organization})"


class EmissionCalculation(models.Model):
    """
    Immutable, factor-pinned, explainable CO2e result for an EmissionRecord.
    This is the SOLE source of truth for CO2e (never denormalized onto the
    locked EmissionRecord). Exactly one row per record has is_current=True.
    """
    class ResolutionStatus(models.TextChoices):
        CALCULATED = "CALCULATED", "Calculated"
        UNRESOLVED_NO_FACTOR = "UNRESOLVED_NO_FACTOR", "No matching factor"
        UNRESOLVED_NO_ACTIVITY_TYPE = "UNRESOLVED_NO_ACTIVITY_TYPE", "No activity-type mapping"
        EXCLUDED_FAILED = "EXCLUDED_FAILED", "Excluded (failed validation)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="emission_calculations"
    )
    emission_record = models.ForeignKey(
        "ingestion.EmissionRecord", on_delete=models.CASCADE, related_name="calculations"
    )
    is_current = models.BooleanField(default=True)

    activity_type = models.ForeignKey(
        ActivityType, null=True, blank=True, on_delete=models.PROTECT, related_name="calculations"
    )
    emission_factor = models.ForeignKey(
        EmissionFactor, null=True, blank=True, on_delete=models.PROTECT, related_name="calculations",
        help_text="PROTECTed: a factor used by a calculation cannot be deleted",
    )

    # Immutable snapshot (survives archival of reference rows)
    factor_publisher = models.CharField(max_length=16, blank=True)
    factor_version = models.CharField(max_length=64, blank=True)
    factor_value = models.DecimalField(max_digits=30, decimal_places=12, null=True, blank=True)
    factor_unit = models.CharField(max_length=32, blank=True)
    activity_quantity = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    activity_unit = models.CharField(max_length=32, blank=True)

    co2e_kg = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    co2e_tonnes = models.DecimalField(max_digits=20, decimal_places=9, null=True, blank=True)
    gas_breakdown = models.JSONField(default=dict, blank=True)

    # Analytic dimensions — denormalized onto the fact table so dashboards run
    # indexed SUM/GROUP BY (by scope, over time) with no joins into the record.
    scope = models.CharField(max_length=20, choices=Scope.choices, blank=True)
    reporting_date = models.DateField(
        null=True, blank=True, help_text="Activity/emission date (drives time-series)"
    )
    reporting_month = models.DateField(
        null=True, blank=True, help_text="First day of reporting_date's month (bucketing)"
    )

    # Explainability — self-contained breakdown, rendered without recomputation
    calculation_trace = models.JSONField(default=dict, blank=True)

    resolution_status = models.CharField(
        max_length=32, choices=ResolutionStatus.choices, default=ResolutionStatus.CALCULATED
    )
    engine_version = models.CharField(max_length=16, default="1.0")
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Emission Calculation"
        ordering = ["-calculated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["emission_record"],
                condition=models.Q(is_current=True),
                name="unique_current_calc_per_record",
            )
        ]
        indexes = [
            models.Index(fields=["organization", "is_current"], name="ix_calc_org_current"),
            models.Index(fields=["emission_record", "is_current"], name="ix_calc_record_current"),
            models.Index(fields=["organization", "resolution_status"], name="ix_calc_org_status"),
            models.Index(fields=["organization", "activity_type"], name="ix_calc_org_acttype"),
            # Analytic aggregation / time-series (Phase 4 Metrics API)
            models.Index(fields=["organization", "is_current", "scope"], name="ix_calc_org_scope"),
            models.Index(fields=["organization", "is_current", "reporting_date"], name="ix_calc_org_rdate"),
            models.Index(fields=["organization", "is_current", "reporting_month"], name="ix_calc_org_rmonth"),
        ]

    def __str__(self):
        return f"Calc({self.emission_record_id}) {self.co2e_kg} kgCO2e [{self.resolution_status}]"
