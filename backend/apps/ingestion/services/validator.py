"""
validator.py

Row-level validation against source-specific business rules.

WHY a dedicated validator:
  Parsers should only care about structure (can I read the file?).
  Validators care about semantics (does this row make business sense?).
  Keeping them separate means the validator can be unit tested with
  synthetic ParsedRow objects without ever touching a real file.

TWO tiers of validation:
  FAILED      - Unrecoverable data error.  The row cannot contribute to
                any calculation.  Example: quantity is None, unknown unit.
  SUSPICIOUS  - Structurally valid but anomalous.  The row is ingested and
                stored, but flagged for mandatory analyst review before approval.
                Example: quantity is 10× the batch median, date is 18 months old.
"""
import statistics
from dataclasses import dataclass, field
from datetime import date
from .base_parser import ParsedRow

# ---------------------------------------------------------------------------
# Lookup sets for source-specific validation
# ---------------------------------------------------------------------------
VALID_SAP_UNITS: frozenset[str] = frozenset({
    "L", "LTR",          # Liters
    "M3",                # Cubic metres (natural gas)
    "KG",                # Kilograms
    "T",                 # Metric tonnes
    "GAL",               # US gallons
    "MMBTU",             # Million BTU (natural gas)
    "MJ", "GJ",          # Megajoules / Gigajoules
})

VALID_UTILITY_UNITS: frozenset[str] = frozenset({
    "kWh", "KWH", "MWh", "MWH",
})

VALID_TRAVEL_MODES: frozenset[str] = frozenset({
    "FLIGHT", "RAIL", "CAR_RENTAL", "TAXI", "HOTEL", "FERRY", "BUS",
})

# Rows whose quantity exceeds this multiple of the batch median are suspicious.
SUSPICIOUS_QUANTITY_MULTIPLIER: float = 5.0

# Dates this many days in the past trigger a suspicious flag.
SUSPICIOUS_DATE_AGE_DAYS: int = 400


@dataclass
class ValidationResult:
    """
    Per-row result from the validator.

    is_failed     : True → store as RecordStatus.FAILED (excluded from analytics)
    is_suspicious : True → store as RecordStatus.SUSPICIOUS (analyst must review)
    errors        : Structured {field: [messages]} dict stored in
                    EmissionRecord.validation_errors
    """
    row_index: int
    is_failed: bool = False
    is_suspicious: bool = False
    errors: dict = field(default_factory=dict)

    def mark_failed(self, field_name: str, message: str) -> None:
        """Mark this row as unrecoverably bad."""
        self.is_failed = True
        self.errors.setdefault(field_name, []).append(message)

    def mark_suspicious(self, field_name: str, message: str) -> None:
        """Flag row for analyst review; does not exclude from normalisation."""
        self.is_suspicious = True
        self.errors.setdefault(field_name, []).append(f"[SUSPICIOUS] {message}")


class RowValidator:
    """
    Validates a single ParsedRow.  Stateless — create once, call many times.

    Usage:
        validator = RowValidator()
        batch_quantities = [r.quantity for r in parsed_rows if r.quantity]
        result = validator.validate(row, batch_quantities)
    """

    def validate(
        self,
        row: ParsedRow,
        batch_quantities: list[float],
    ) -> ValidationResult:
        result = ValidationResult(row_index=row.row_index)

        if row.source_type == "SAP_FUEL":
            self._validate_sap(row, result)
        elif row.source_type == "UTILITY_ELECTRICITY":
            self._validate_utility(row, result)
        elif row.source_type == "CORP_TRAVEL":
            self._validate_travel(row, result)

        # Cross-source: batch-level quantity outlier detection
        self._check_quantity_outlier(row, result, batch_quantities)

        return result

    # ------------------------------------------------------------------
    # Source-specific rule sets
    # ------------------------------------------------------------------

    def _validate_sap(self, row: ParsedRow, result: ValidationResult) -> None:
        # Quantity checks
        if row.quantity is None:
            result.mark_failed("quantity", "Quantity could not be parsed from the source row.")
        elif row.quantity == 0:
            result.mark_failed("quantity", "Zero quantity rows are not valid fuel transactions.")
        elif row.quantity < 0:
            result.mark_failed("quantity", f"Negative quantity ({row.quantity}) is physically impossible.")

        # Unit checks
        if not row.unit:
            result.mark_failed("unit", "Unit of measure is missing.")
        elif row.unit.upper() not in VALID_SAP_UNITS:
            result.mark_failed(
                "unit",
                f"Unit '{row.unit}' is not recognised. Cannot normalise to Liters."
                f" Valid options: {sorted(VALID_SAP_UNITS)}",
            )

        # Date checks
        if not row.date:
            result.mark_suspicious("posting_date", "Posting date could not be parsed.")
        else:
            self._check_date_age(row.date, result, "posting_date")

        # Site / material
        if not row.site_reference:
            result.mark_suspicious("plant_code", "Plant code (Werk) is missing — cannot attribute to a site.")
        if not row.material_or_mode:
            result.mark_suspicious("material_code", "Material code is missing — fuel type is unknown.")

    def _validate_utility(self, row: ParsedRow, result: ValidationResult) -> None:
        # Quantity checks
        if row.quantity is None:
            result.mark_failed("quantity", "Energy consumption value is missing or unparseable.")
        elif row.quantity < 0:
            result.mark_failed("quantity", f"Negative consumption ({row.quantity}) is physically impossible.")
        elif row.quantity == 0:
            result.mark_suspicious(
                "quantity",
                "Zero consumption — verify this site was active during this period.",
            )

        # Date / billing period checks
        if not row.date:
            result.mark_suspicious("period_start", "Billing period start date could not be parsed.")

        period_start = row.extra.get("period_start") or row.date
        period_end = row.extra.get("period_end")
        if period_start and period_end:
            try:
                start = date.fromisoformat(period_start)
                end = date.fromisoformat(period_end)
                delta = (end - start).days
                if delta < 0:
                    result.mark_failed("period", "Billing period end is before period start.")
                elif delta > 35:
                    result.mark_suspicious(
                        "period",
                        f"Billing period is {delta} days — unusually long for monthly billing.",
                    )
            except ValueError:
                pass

        # Site reference
        if not row.site_reference:
            result.mark_suspicious("meter_mpan", "Meter MPAN or site name is missing.")

    def _validate_travel(self, row: ParsedRow, result: ValidationResult) -> None:
        travel_mode = (row.material_or_mode or "").upper()

        # Travel mode checks
        if not travel_mode:
            result.mark_failed("travel_mode", "Travel mode is missing.")
        elif travel_mode not in VALID_TRAVEL_MODES:
            result.mark_failed(
                "travel_mode",
                f"Unknown travel mode '{travel_mode}'. "
                f"Valid options: {sorted(VALID_TRAVEL_MODES)}",
            )

        # Distance checks
        if row.quantity is None:
            if travel_mode == "FLIGHT":
                result.mark_failed(
                    "distance_km",
                    "Distance is null and origin/destination IATA codes could not "
                    "be resolved for Haversine derivation.",
                )
            else:
                result.mark_failed(
                    "distance_km",
                    "Distance is missing and cannot be derived for non-flight modes.",
                )
        elif row.quantity <= 0:
            result.mark_failed("distance_km", f"Distance must be > 0. Got: {row.quantity}")
        elif travel_mode == "FLIGHT" and row.quantity > 20_000:
            result.mark_suspicious(
                "distance_km",
                f"Flight distance {row.quantity:.0f} km exceeds the maximum "
                "physically possible single-leg range (~20,000 km).",
            )

        # If distance was derived via Haversine, flag for analyst review
        if row.extra.get("distance_derived"):
            result.mark_suspicious(
                "distance_km",
                "Distance was estimated via Haversine formula from IATA airport "
                "coordinates — verify against official booking documentation.",
            )

        # Date checks
        if not row.date:
            result.mark_suspicious("travel_date", "Travel date could not be parsed.")

        # Employee ID
        if not row.site_reference:
            result.mark_suspicious("employee_id", "Employee ID is missing.")

    # ------------------------------------------------------------------
    # Cross-source helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_date_age(date_str: str, result: ValidationResult, field_name: str) -> None:
        try:
            parsed = date.fromisoformat(date_str)
            today = date.today()
            delta = (today - parsed).days
            if delta > SUSPICIOUS_DATE_AGE_DAYS:
                result.mark_suspicious(
                    field_name,
                    f"Date is {delta} days in the past — confirm this falls within "
                    "the intended reporting period.",
                )
            elif delta < 0:
                result.mark_suspicious(field_name, "Date is in the future.")
        except ValueError:
            pass

    @staticmethod
    def _check_quantity_outlier(
        row: ParsedRow,
        result: ValidationResult,
        batch_quantities: list[float],
    ) -> None:
        """Flag rows whose quantity is SUSPICIOUS_QUANTITY_MULTIPLIER× the batch median."""
        if row.quantity is None or not batch_quantities:
            return
        # Baseline of "normal" consumption = positive quantities only; negative
        # or zero values are invalid and must not skew the median. Use a true
        # median (statistics.median averages the middle pair for even counts,
        # unlike the previous sorted_q[len//2] which was biased high).
        baseline = [q for q in batch_quantities if q is not None and q > 0]
        if not baseline:
            return
        median = statistics.median(baseline)
        if median > 0 and row.quantity > SUSPICIOUS_QUANTITY_MULTIPLIER * median:
            result.mark_suspicious(
                "quantity",
                f"Quantity {row.quantity:.2f} is more than "
                f"{SUSPICIOUS_QUANTITY_MULTIPLIER}× the batch median "
                f"({median:.2f}).",
            )
