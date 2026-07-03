"""
travel_parser.py

Parses corporate travel JSON exports from travel management companies (TMCs).

WHY JSON and not CSV:
  Modern TMC APIs (Concur, Egencia, BCD Travel) produce document-oriented
  JSON because trips have nested attributes (segments, legs, cost centres).
  A flat CSV representation loses structure or requires problematic multi-value
  columns.

WHY Haversine distance derivation:
  TMC exports frequently omit distance_km for flights — they record the
  ticket, not the route geometry.  IATA airport codes give us precise
  coordinates; the Haversine formula gives great-circle distance accurate
  to ~0.5% — more than sufficient for ESG estimation.  Derived distances
  are flagged SUSPICIOUS so analysts can verify against booking records.
"""
import json
import math
from datetime import datetime
from .base_parser import BaseParser, ParsedRow

# ---------------------------------------------------------------------------
# IATA airport coordinate lookup (lat, lon in decimal degrees)
# Expand this table as clients report unknown airports.
# ---------------------------------------------------------------------------
IATA_COORDINATES: dict[str, tuple[float, float]] = {
    # United Kingdom
    "LHR": (51.477500, -0.461389),
    "LGW": (51.148056, -0.190278),
    "MAN": (53.353333, -2.275000),
    "EDI": (55.950278, -3.372500),
    "BRS": (51.382500, -2.719167),
    "BHX": (52.453889, -1.748056),
    "GLA": (55.871944, -4.433056),
    # Europe
    "CDG": (49.009722,   2.547778),
    "ORY": (48.723333,   2.379444),
    "AMS": (52.308056,   4.764167),
    "FRA": (50.033333,   8.570556),
    "MUC": (48.353889,  11.786111),
    "ZRH": (47.464722,   8.549167),
    "VIE": (48.110833,  16.570833),
    "MAD": (40.493556,  -3.566764),
    "BCN": (41.297078,   2.078464),
    "FCO": (41.800278,  12.238889),
    "MXP": (45.630556,   8.723056),
    "ARN": (59.651944,  17.918611),
    "CPH": (55.617917,  12.655972),
    "HEL": (60.317222,  24.963333),
    # North America
    "JFK": (40.639722, -73.778889),
    "EWR": (40.689722, -74.174444),
    "LGA": (40.777222, -73.872778),
    "LAX": (33.942500, -118.408056),
    "ORD": (41.978603, -87.904842),
    "DFW": (32.896944, -97.038056),
    "SFO": (37.618889, -122.375000),
    "BOS": (42.364722, -71.005278),
    "MIA": (25.795833, -80.287500),
    "YYZ": (43.677222, -79.630556),
    "YVR": (49.193889, -123.184444),
    # Middle East & Asia
    "DXB": (25.252778,  55.364444),
    "AUH": (24.432972,  54.651138),
    "DOH": (25.273056,  51.608056),
    "SIN": (1.359167,  103.989444),
    "HKG": (22.308889, 113.914722),
    "NRT": (35.764722, 140.386389),
    "HND": (35.549167, 139.779722),
    "ICN": (37.469075, 126.450517),
    "PEK": (40.080111, 116.584556),
    "PVG": (31.143378, 121.805214),
    "BOM": (19.088689,  72.868092),
    "DEL": (28.556722,  77.100544),
    "BLR": (13.198889,  77.705833),
    # Oceania
    "SYD": (-33.946111, 151.177222),
    "MEL": (-37.673333, 144.843333),
    "BNE": (-27.384167, 153.117222),
}

EARTH_RADIUS_KM = 6371.0

# NOTE: valid travel modes are enforced in the validator (RowValidator), which
# is the single source of truth. The parser deliberately does not re-validate.


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class TravelParser(BaseParser):
    """
    Parses corporate travel JSON arrays.

    Expected root structure: a JSON array of trip objects.

    Handles:
    - Missing distance_km: derives from IATA coordinates for flights (flags SUSPICIOUS)
    - Multiple travel modes with different semantic meanings
    - YYYY-MM-DD travel dates
    - Cost fields preserved in raw_data_payload but not used for normalisation
    """

    source_type = "CORP_TRAVEL"

    def parse(self, file_path: str) -> tuple[list[ParsedRow], list[dict]]:
        rows: list[ParsedRow] = []
        parse_errors: list[dict] = []

        with open(file_path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                parse_errors.append({
                    "row_index": 0,
                    "raw_data": {},
                    "error": f"Invalid JSON: {exc}",
                })
                return rows, parse_errors

        if not isinstance(data, list):
            parse_errors.append({
                "row_index": 0,
                "raw_data": {},
                "error": "Expected a JSON array at the root level.",
            })
            return rows, parse_errors

        for row_index, record in enumerate(data, start=1):
            if not isinstance(record, dict):
                parse_errors.append({
                    "row_index": row_index,
                    "raw_data": record,
                    "error": "Each travel record must be a JSON object.",
                })
                continue

            travel_mode = str(record.get("travel_mode", "")).upper().strip()
            origin = str(record.get("origin", "")).upper().strip()
            destination = str(record.get("destination", "")).upper().strip()

            distance_km, was_derived = self._resolve_distance(
                record.get("distance_km"),
                origin,
                destination,
                travel_mode,
            )

            travel_date = self._parse_date(str(record.get("travel_date", "")))

            rows.append(ParsedRow(
                row_index=row_index,
                source_type=self.source_type,
                raw_data=dict(record),
                quantity=distance_km,
                unit="km",
                date=travel_date,
                site_reference=str(record.get("employee_id", "")).strip() or None,
                material_or_mode=travel_mode or None,
                extra={
                    "trip_id": record.get("trip_id"),
                    "origin": origin,
                    "destination": destination,
                    "travel_class": record.get("class"),
                    "airline_code": record.get("airline_code"),
                    "flight_number": record.get("flight_number"),
                    "cost_usd": record.get("cost_usd"),
                    "booking_date": record.get("booking_date"),
                    "distance_derived": was_derived,
                },
            ))

        return rows, parse_errors

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_distance(
        raw_distance,
        origin: str,
        destination: str,
        travel_mode: str,
    ) -> tuple[float | None, bool]:
        """
        Returns (distance_km, was_derived).

        was_derived=True means the distance came from Haversine, not the source
        data — the validator uses this flag to set is_suspicious=True.
        """
        if raw_distance is not None:
            try:
                val = float(raw_distance)
                if val > 0:
                    return val, False
            except (TypeError, ValueError):
                pass

        # Haversine derivation — flights only
        if travel_mode == "FLIGHT":
            o_coords = IATA_COORDINATES.get(origin)
            d_coords = IATA_COORDINATES.get(destination)
            if o_coords and d_coords:
                dist = haversine_km(*o_coords, *d_coords)
                return round(dist, 2), True

        return None, False

    @staticmethod
    def _parse_date(date_str: str) -> str | None:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(date_str.strip(), fmt).date().isoformat()
            except ValueError:
                continue
        return None
