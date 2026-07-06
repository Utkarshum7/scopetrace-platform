"""
Emission-factor importers (Strategy pattern, mirroring the ingestion parsers).

A canonical CSV format is shared by all publishers; publisher-specific raw
formats can be normalized by overriding `parse` in a subclass later without
touching the import command. Columns:

    activity_type, unit, co2e_per_unit, region, valid_from, valid_to,
    methodology, source_ref
"""
import csv
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class FactorImportError(Exception):
    """Raised when a source file cannot be parsed into valid factor rows."""


@dataclass
class FactorRow:
    activity_type_code: str
    unit: str
    co2e_per_unit: Decimal
    region_code: str = ""
    valid_from: date | None = None
    valid_to: date | None = None
    methodology: str = ""
    source_ref: str = ""


def _parse_date(value):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


class BaseFactorImporter(ABC):
    publisher = ""

    @abstractmethod
    def parse(self, text: str) -> list:
        ...


class CsvFactorImporter(BaseFactorImporter):
    """Canonical CSV importer used by all publishers in Phase 3."""

    def parse(self, text: str) -> list:
        rows = []
        reader = csv.DictReader(io.StringIO(text))
        for i, raw in enumerate(reader, start=1):
            code = (raw.get("activity_type") or "").strip()
            if not code:
                continue
            raw_value = (raw.get("co2e_per_unit") or "").strip()
            try:
                value = Decimal(raw_value)
            except (InvalidOperation, ValueError):
                raise FactorImportError(f"Row {i}: invalid co2e_per_unit '{raw_value}'.")
            if value <= 0:
                raise FactorImportError(f"Row {i}: co2e_per_unit must be > 0 (got {value}).")
            unit = (raw.get("unit") or "").strip()
            if not unit:
                raise FactorImportError(f"Row {i}: unit is required.")
            rows.append(FactorRow(
                activity_type_code=code,
                unit=unit,
                co2e_per_unit=value,
                region_code=(raw.get("region") or "").strip(),
                valid_from=_parse_date(raw.get("valid_from")),
                valid_to=_parse_date(raw.get("valid_to")),
                methodology=(raw.get("methodology") or "").strip(),
                source_ref=(raw.get("source_ref") or "").strip(),
            ))
        return rows


class DefraImporter(CsvFactorImporter):
    publisher = "DEFRA"


class EpaImporter(CsvFactorImporter):
    publisher = "EPA"


class IpccImporter(CsvFactorImporter):
    publisher = "IPCC"


IMPORTER_REGISTRY = {
    "DEFRA": DefraImporter,
    "EPA": EpaImporter,
    "IPCC": IpccImporter,
    "COUNTRY": CsvFactorImporter,
    "CUSTOM": CsvFactorImporter,
}
