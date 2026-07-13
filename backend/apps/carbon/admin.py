from django.contrib import admin

from apps.carbon.models import (
    ActivityMapping,
    ActivityType,
    EmissionCalculation,
    EmissionFactor,
    EmissionFactorDataset,
    GwpSet,
    OrgFactorPolicy,
    Region,
    UnitConversion,
)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "parent")
    search_fields = ("code", "name")


@admin.register(ActivityType)
class ActivityTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "default_scope", "base_unit")
    list_filter = ("default_scope",)
    search_fields = ("code", "name")


class EmissionFactorInline(admin.TabularInline):
    model = EmissionFactor
    extra = 0
    fields = ("activity_type", "region", "unit", "co2e_per_unit", "valid_from", "valid_to")


@admin.register(EmissionFactorDataset)
class EmissionFactorDatasetAdmin(admin.ModelAdmin):
    list_display = ("publisher", "version", "region", "status", "valid_from", "import_timestamp", "imported_by")
    list_filter = ("publisher", "status")
    search_fields = ("name", "version", "checksum")
    readonly_fields = ("import_timestamp", "checksum", "imported_by")
    inlines = [EmissionFactorInline]


@admin.register(EmissionFactor)
class EmissionFactorAdmin(admin.ModelAdmin):
    list_display = ("activity_type", "co2e_per_unit", "unit", "region", "dataset")
    list_filter = ("dataset__publisher", "activity_type")
    search_fields = ("activity_type__code",)


@admin.register(UnitConversion)
class UnitConversionAdmin(admin.ModelAdmin):
    list_display = ("from_unit", "to_unit", "dimension", "factor")
    list_filter = ("dimension",)


@admin.register(ActivityMapping)
class ActivityMappingAdmin(admin.ModelAdmin):
    list_display = ("data_source_type", "match_key", "activity_type", "region", "priority")
    list_filter = ("data_source_type",)


@admin.register(OrgFactorPolicy)
class OrgFactorPolicyAdmin(admin.ModelAdmin):
    list_display = ("organization", "preferred_publisher", "default_region", "strict_mode")


@admin.register(GwpSet)
class GwpSetAdmin(admin.ModelAdmin):
    list_display = ("name", "gwp_co2", "gwp_ch4", "gwp_n2o")


@admin.register(EmissionCalculation)
class EmissionCalculationAdmin(admin.ModelAdmin):
    list_display = (
        "emission_record", "organization", "co2e_kg", "co2e_tonnes",
        "resolution_status", "is_current", "calculated_at",
    )
    list_filter = ("resolution_status", "is_current", "organization")
    search_fields = ("emission_record__id",)
    readonly_fields = ("calculated_at",)
