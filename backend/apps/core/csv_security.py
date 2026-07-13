"""
Phase 6f — CSV/"formula injection" mitigation (OWASP-documented: a cell
value opened in Excel/Sheets that starts with =, +, -, @, tab, or a
carriage return can be interpreted as a formula rather than literal text).
Shared by every CSV export this project has -- apps.ingestion.export_views
(RecordExportView) and apps.carbon.report_views (ComplianceReportCSVView).

Real, not theoretical, for this codebase: RecordExportView writes
UploadBatch.file_name into a CSV cell verbatim, and file_name is
user-controlled at upload time (the client sends whatever name the
uploaded file had).
"""

_DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_csv_cell(value):
    """Prefixes a single quote onto any STRING value that starts with a
    formula-triggering character -- Excel/Sheets treat a leading `'` as
    "force this cell to plain text", neutralizing the payload while
    leaving the value visually almost unchanged.

    Deliberately only acts on `str` -- numeric fields (Decimal/int/None)
    are written from typed columns elsewhere in this codebase and must
    pass through completely untouched: a negative number's legitimate
    leading '-' is not a CSV injection risk and must never be corrupted
    into a quoted string.
    """
    if isinstance(value, str) and value.startswith(_DANGEROUS_PREFIXES):
        return "'" + value
    return value
