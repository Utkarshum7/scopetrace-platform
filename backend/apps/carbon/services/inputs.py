"""Build an ActivityInput from a stored EmissionRecord (used by backfill + recalc)."""
from datetime import datetime

from apps.carbon.services.pipeline import ActivityInput

_DATE_KEYS = {
    "buchungsdatum", "posting_date", "travel_date", "billing period start",
    "billing_period_start", "period_start", "start date", "date",
}
_DATE_FORMATS = ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"]
_MATCH_KEYS = ("material", "material_code", "travel_mode", "mode")


def extract_activity_date(payload):
    for key, value in (payload or {}).items():
        if str(key).strip().lower() in _DATE_KEYS and value:
            for fmt in _DATE_FORMATS:
                try:
                    return datetime.strptime(str(value).strip(), fmt).date()
                except ValueError:
                    continue
    return None


def extract_match_keys(payload):
    keys = []
    for key, value in (payload or {}).items():
        if str(key).strip().lower() in _MATCH_KEYS and value:
            keys.append(str(value))
    return keys


def activity_input_from_record(record):
    """`record` must have `batch.data_source` available (select_related)."""
    payload = record.raw_data_payload or {}
    return ActivityInput(
        record_id=record.id,
        organization_id=record.organization_id,
        source_type=record.batch.data_source.source_type,
        quantity=record.normalized_value if record.normalized_value is not None else 0,
        unit=record.normalized_unit or "",
        scope=record.scope_category or "",
        match_keys=extract_match_keys(payload),
        activity_date=extract_activity_date(payload),
        status=record.status,
    )
