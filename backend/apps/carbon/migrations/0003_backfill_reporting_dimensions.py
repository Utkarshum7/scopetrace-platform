"""
Data migration: populate scope / reporting_date / reporting_month on existing
EmissionCalculation rows (added in 0002). Updates the calculation table in place
(bulk_update) — it never touches EmissionRecord, so the approval audit-lock is
respected. Date extraction is frozen inline so this migration is stable.
"""
from datetime import datetime

from django.db import migrations

_DATE_KEYS = {
    "buchungsdatum", "posting_date", "travel_date", "billing period start",
    "billing_period_start", "period_start", "start date", "date",
}
_DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]


def _extract_date(payload):
    for key, value in (payload or {}).items():
        if str(key).strip().lower() in _DATE_KEYS and value:
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(str(value).strip(), fmt).date()
                except ValueError:
                    continue
    return None


def populate(apps, schema_editor):
    EmissionCalculation = apps.get_model("carbon", "EmissionCalculation")
    batch = []
    qs = EmissionCalculation.objects.select_related("emission_record").iterator(chunk_size=1000)
    for calc in qs:
        record = calc.emission_record
        calc.scope = (record.scope_category or "") if record else ""
        d = _extract_date(record.raw_data_payload or {}) if record else None
        calc.reporting_date = d
        calc.reporting_month = d.replace(day=1) if d else None
        batch.append(calc)
        if len(batch) >= 1000:
            EmissionCalculation.objects.bulk_update(
                batch, ["scope", "reporting_date", "reporting_month"]
            )
            batch = []
    if batch:
        EmissionCalculation.objects.bulk_update(
            batch, ["scope", "reporting_date", "reporting_month"]
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("carbon", "0002_emissioncalculation_reporting_date_and_more"),
    ]
    operations = [
        migrations.RunPython(populate, noop),
    ]
