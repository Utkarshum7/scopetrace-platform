import tempfile
import json
from decimal import Decimal
from datetime import date, timedelta
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status as drf_status
from apps.core.models import Organization, DataSource
from apps.ingestion.models import UploadBatch, EmissionRecord
from apps.audit.models import AuditTrail
from apps.audit.services import append_entry
from apps.ingestion.services.base_parser import ParsedRow
from apps.ingestion.services.sap_parser import SAPFuelParser
from apps.ingestion.services.utility_parser import UtilityElectricityParser
from apps.ingestion.services.travel_parser import TravelParser
from apps.ingestion.services.validator import RowValidator
from apps.ingestion.services.normalizer import NormalizationService
from apps.ingestion.services.ingestion_service import IngestionService

User = get_user_model()


class ESGDomainModelTestCase(TestCase):

    def setUp(self):
        # Create standard test data
        self.org = Organization.objects.create(name="Acme Corp")
        self.user = User.objects.create_user(username="analyst", password="password")
        self.data_source = DataSource.objects.create(
            organization=self.org,
            name="SAP Fuel Export",
            source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.batch = UploadBatch.objects.create(
            organization=self.org,
            data_source=self.data_source,
            file_name="sap_q1_2026.csv",
            uploaded_by=self.user,
            total_rows=1,
        )

    def test_emission_record_lifecycle_and_audit_locking(self):
        # 1. Ingest a record in DRAFT state
        record = EmissionRecord.objects.create(
            organization=self.org,
            batch=self.batch,
            row_index=1,
            raw_data_payload={"fuel_type": "Diesel", "quantity": "5000", "unit": "Liters"},
            status=EmissionRecord.RecordStatus.DRAFT,
            normalized_value=5000.0,
            normalized_unit="Liters",
            scope_category=EmissionRecord.ScopeCategory.SCOPE_1,
        )
        self.assertEqual(record.status, EmissionRecord.RecordStatus.DRAFT)

        # 2. Update is permitted while in DRAFT
        record.normalized_value = 5200.0
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.normalized_value, 5200.0)

        # 3. Transition to APPROVED state
        record.status = EmissionRecord.RecordStatus.APPROVED
        record.approved_by = self.user
        record.save()
        record.refresh_from_db()
        self.assertEqual(record.status, EmissionRecord.RecordStatus.APPROVED)

        # 4. Attempt to modify fields on locked APPROVED record (should fail)
        record.normalized_value = 6000.0
        with self.assertRaises(ValidationError) as ctx:
            record.save()
        self.assertIn(
            "No modifications are permitted on locked transaction logs", str(ctx.exception)
        )

        # Check values are unchanged in DB
        record.refresh_from_db()
        self.assertEqual(record.normalized_value, 5200.0)

    def test_audit_trail_immutability(self):
        # 1. Create an audit log entry — via append_entry() (Phase 6a), the
        # only sanctioned creation path now that entries are hash-chained;
        # AuditTrail.objects.create() directly would fail full_clean() with
        # sequence/prev_hash/entry_hash unset.
        log = append_entry(
            organization=self.org,
            action="RECORD_INGEST",
            changed_by=self.user,
            changes={"status": ["", "DRAFT"]},
            reason="Initial data ingestion",
        )
        self.assertIsNotNone(log.id)

        # 2. Modifying the audit log entry is blocked
        log.reason = "Altered justification"
        with self.assertRaises(ValidationError) as ctx:
            log.save()
        self.assertIn(
            "Audit logs are read-only and cannot be altered or modified", str(ctx.exception)
        )

        # 3. Deleting the audit log entry is blocked
        with self.assertRaises(ValidationError) as ctx:
            log.delete()
        self.assertIn("Audit logs are append-only and cannot be deleted", str(ctx.exception))


class ServiceLayerTestCase(TestCase):

    def setUp(self):
        self.org = Organization.objects.create(name="Service Test Org")
        self.user = User.objects.create_user(username="service_analyst", password="password")

        self.sap_ds = DataSource.objects.create(
            organization=self.org,
            name="SAP Fuel",
            source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.utility_ds = DataSource.objects.create(
            organization=self.org,
            name="Utility Electricity",
            source_type=DataSource.SourceType.UTILITY_ELECTRICITY,
        )
        self.travel_ds = DataSource.objects.create(
            organization=self.org,
            name="Travel JSON",
            source_type=DataSource.SourceType.CORP_TRAVEL,
        )

    def test_sap_parser_success(self):
        today_str = date.today().strftime("%d.%m.%Y")
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%d-%m-%Y")

        csv_data = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            f"DE01;{today_str};DSL;Diesel;1.200,50;L;1500.00\n"
            f"DE02;{yesterday_str};GAS;Gas;500,00;M3;600.00\n"
        )
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            temp_name = f.name

        try:
            parser = SAPFuelParser()
            rows, parse_errors = parser.parse(temp_name)

            self.assertEqual(len(rows), 2)
            self.assertEqual(len(parse_errors), 0)

            self.assertEqual(rows[0].quantity, 1200.5)
            self.assertEqual(rows[0].unit, "L")
            self.assertEqual(rows[0].date, date.today().isoformat())
            self.assertEqual(rows[0].site_reference, "DE01")

            self.assertEqual(rows[1].quantity, 500.0)
            self.assertEqual(rows[1].unit, "M3")
            self.assertEqual(rows[1].date, (date.today() - timedelta(days=1)).isoformat())
        finally:
            import os

            os.remove(temp_name)

    def test_sap_parser_missing_headers(self):
        today_str = date.today().strftime("%d.%m.%Y")
        csv_data = f"Werk;Buchungsdatum;Material\nDE01;{today_str};DSL\n"
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            temp_name = f.name

        try:
            parser = SAPFuelParser()
            rows, parse_errors = parser.parse(temp_name)
            self.assertEqual(len(rows), 0)
            self.assertEqual(len(parse_errors), 1)
            self.assertIn("Missing required columns", parse_errors[0]["error"])
        finally:
            import os

            os.remove(temp_name)

    def test_utility_parser_success(self):
        start_str = (date.today() - timedelta(days=30)).strftime("%d/%m/%Y")
        end_str = date.today().strftime("%d/%m/%Y")
        start_iso = (date.today() - timedelta(days=30)).isoformat()
        end_iso = date.today().isoformat()

        csv_data = (
            "Account Number,Meter MPAN,Site Name,Billing Period Start,Billing Period End,"
            "kWh Consumed,MWh Consumed,Total Cost (GBP)\n"
            f"12345,MPAN001,London HQ,{start_str},{end_str},1200,,240.00\n"
            f"12345,MPAN001,London HQ,{start_iso},{end_iso},,2.5,500.00\n"
        )
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            temp_name = f.name

        try:
            parser = UtilityElectricityParser()
            rows, parse_errors = parser.parse(temp_name)

            self.assertEqual(len(rows), 2)
            self.assertEqual(len(parse_errors), 0)

            self.assertEqual(rows[0].quantity, 1200.0)
            self.assertEqual(rows[0].unit, "kWh")
            self.assertEqual(rows[0].date, start_iso)

            self.assertEqual(rows[1].quantity, 2.5)
            self.assertEqual(rows[1].unit, "MWh")
        finally:
            import os

            os.remove(temp_name)

    def test_travel_parser_success(self):
        travel_date1 = date.today().isoformat()
        travel_date2 = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
        travel_iso2 = (date.today() - timedelta(days=1)).isoformat()

        travel_data = [
            {
                "trip_id": "T001",
                "travel_mode": "FLIGHT",
                "origin": "LHR",
                "destination": "JFK",
                "distance_km": None,
                "travel_date": travel_date1,
                "employee_id": "EMP001",
                "class": "BUSINESS",
            },
            {
                "trip_id": "T002",
                "travel_mode": "RAIL",
                "origin": "LON",
                "destination": "PAR",
                "distance_km": 490.0,
                "travel_date": travel_date2,
                "employee_id": "EMP002",
            },
        ]
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".json", delete=False) as f:
            json.dump(travel_data, f)
            temp_name = f.name

        try:
            parser = TravelParser()
            rows, parse_errors = parser.parse(temp_name)

            self.assertEqual(len(rows), 2)
            self.assertEqual(len(parse_errors), 0)

            # Haversine check (LHR to JFK is approx 5570 km)
            self.assertIsNotNone(rows[0].quantity)
            self.assertAlmostEqual(rows[0].quantity, 5570.0, delta=100.0)
            self.assertTrue(rows[0].extra["distance_derived"])

            self.assertEqual(rows[1].quantity, 490.0)
            self.assertFalse(rows[1].extra["distance_derived"])
            self.assertEqual(rows[1].date, travel_iso2)
        finally:
            import os

            os.remove(temp_name)

    def test_row_validator(self):
        validator = RowValidator()
        today_iso = date.today().isoformat()

        # 1. Valid SAP row
        row_valid_sap = ParsedRow(
            row_index=1,
            source_type="SAP_FUEL",
            raw_data={},
            quantity=100.0,
            unit="L",
            date=today_iso,
            site_reference="DE01",
            material_or_mode="DSL",
        )
        res = validator.validate(row_valid_sap, [100.0])
        self.assertFalse(res.is_failed)
        self.assertFalse(res.is_suspicious)

        # 2. Failed negative quantity
        row_neg = ParsedRow(
            row_index=2,
            source_type="SAP_FUEL",
            raw_data={},
            quantity=-10.0,
            unit="L",
            date=today_iso,
            site_reference="DE01",
            material_or_mode="DSL",
        )
        res = validator.validate(row_neg, [-10.0])
        self.assertTrue(res.is_failed)
        self.assertIn("quantity", res.errors)

        # 3. Suspicious old date
        old_iso = (date.today() - timedelta(days=500)).isoformat()
        row_old = ParsedRow(
            row_index=3,
            source_type="SAP_FUEL",
            raw_data={},
            quantity=100.0,
            unit="L",
            date=old_iso,
            site_reference="DE01",
            material_or_mode="DSL",
        )
        res = validator.validate(row_old, [100.0])
        self.assertFalse(res.is_failed)
        self.assertTrue(res.is_suspicious)
        self.assertIn("posting_date", res.errors)

        # 4. Outlier detection
        row_outlier = ParsedRow(
            row_index=4,
            source_type="SAP_FUEL",
            raw_data={},
            quantity=600.0,
            unit="L",
            date=today_iso,
            site_reference="DE01",
            material_or_mode="DSL",
        )
        res = validator.validate(row_outlier, [10.0, 10.0, 10.0, 600.0])
        self.assertFalse(res.is_failed)
        self.assertTrue(res.is_suspicious)
        self.assertIn("quantity", res.errors)

    def test_normalization_service(self):
        normalizer = NormalizationService()

        # SAP L -> L (1.0)
        row_sap_l = ParsedRow(
            row_index=1, source_type="SAP_FUEL", raw_data={}, quantity=100.0, unit="L"
        )
        res = normalizer.normalize(row_sap_l)
        self.assertTrue(res.is_success)
        self.assertEqual(res.value, Decimal("100.000000"))
        self.assertEqual(res.unit, "L")
        self.assertEqual(res.scope_category, "SCOPE_1")

        # SAP M3 -> L (1000.0)
        row_sap_m3 = ParsedRow(
            row_index=2, source_type="SAP_FUEL", raw_data={}, quantity=2.5, unit="M3"
        )
        res = normalizer.normalize(row_sap_m3)
        self.assertTrue(res.is_success)
        self.assertEqual(res.value, Decimal("2500.000000"))

        # Utility MWh -> kWh (1000.0)
        row_ut_mwh = ParsedRow(
            row_index=3, source_type="UTILITY_ELECTRICITY", raw_data={}, quantity=1.5, unit="MWh"
        )
        res = normalizer.normalize(row_ut_mwh)
        self.assertTrue(res.is_success)
        self.assertEqual(res.value, Decimal("1500.000000"))
        self.assertEqual(res.unit, "kWh")
        self.assertEqual(res.scope_category, "SCOPE_2")

        # Travel Rail -> km (1.0)
        row_tr_rail = ParsedRow(
            row_index=4,
            source_type="CORP_TRAVEL",
            raw_data={},
            quantity=100.0,
            unit="km",
            material_or_mode="RAIL",
        )
        res = normalizer.normalize(row_tr_rail)
        self.assertTrue(res.is_success)
        self.assertEqual(res.value, Decimal("100.000000"))
        self.assertEqual(res.unit, "km")
        self.assertEqual(res.scope_category, "SCOPE_3")

        # Travel Flight Business -> km (2.9)
        row_tr_flight = ParsedRow(
            row_index=5,
            source_type="CORP_TRAVEL",
            raw_data={},
            quantity=100.0,
            unit="km",
            material_or_mode="FLIGHT",
            extra={"travel_class": "BUSINESS"},
        )
        res = normalizer.normalize(row_tr_flight)
        self.assertTrue(res.is_success)
        self.assertEqual(res.value, Decimal("290.000000"))

    def test_ingestion_service_end_to_end(self):
        today_str = date.today().strftime("%d.%m.%Y")
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%d.%m.%Y")
        old_str = (date.today() - timedelta(days=500)).strftime("%d.%m.%Y")
        two_days_ago_str = (date.today() - timedelta(days=2)).strftime("%d.%m.%Y")
        three_days_ago_str = (date.today() - timedelta(days=3)).strftime("%d.%m.%Y")

        csv_data = (
            "Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n"
            f"DE01;{today_str};DSL;Diesel;1.200,50;L;1500.00\n"
            f"DE02;{yesterday_str};GAS;Gas;500,00;M3;600.00\n"
            f"DE01;{old_str};DSL;Old Fuel;100.00;L;120.00\n"  # old -> suspicious
            f"DE02;{two_days_ago_str};DSL;Negative;-10.00;L;-12.00\n"  # negative -> failed validation
            f"DE01;{three_days_ago_str};DSL;Invalid;250.00;XYZ;300.00\n"  # invalid unit -> failed validation
        )
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".csv", delete=False) as f:
            f.write(csv_data)
            temp_name = f.name

        try:
            service = IngestionService()
            result = service.ingest(self.sap_ds, temp_name, uploaded_by=self.user)

            # Verify batch status and counts. Phase 5c: any row-level
            # failure yields PARTIALLY_COMPLETED, not COMPLETED — the
            # pipeline itself didn't crash, but not every row succeeded.
            self.assertEqual(result.batch.status, UploadBatch.BatchStatus.PARTIALLY_COMPLETED)
            self.assertEqual(result.total_rows, 5)
            self.assertEqual(result.failed_rows, 2)
            self.assertEqual(result.suspicious_rows, 1)

            # Verify database records
            records = EmissionRecord.objects.filter(batch=result.batch).order_by("row_index")
            self.assertEqual(records.count(), 5)

            # Record 1: Clean
            self.assertEqual(records[0].status, EmissionRecord.RecordStatus.DRAFT)
            self.assertEqual(records[0].normalized_value, Decimal("1200.500000"))

            # Record 2: Clean M3 -> L
            self.assertEqual(records[1].status, EmissionRecord.RecordStatus.DRAFT)
            self.assertEqual(records[1].normalized_value, Decimal("500000.000000"))  # 500 * 1000

            # Record 3: Suspicious (old date)
            self.assertEqual(records[2].status, EmissionRecord.RecordStatus.SUSPICIOUS)
            self.assertTrue(records[2].is_suspicious)
            self.assertEqual(records[2].normalized_value, Decimal("100.000000"))

            # Record 4: Failed validation (negative)
            self.assertEqual(records[3].status, EmissionRecord.RecordStatus.FAILED)
            self.assertIsNone(records[3].normalized_value)

            # Record 5: Failed validation (invalid unit)
            self.assertEqual(records[4].status, EmissionRecord.RecordStatus.FAILED)
            self.assertIsNone(records[4].normalized_value)

        finally:
            import os

            os.remove(temp_name)


# =============================================================================
# Phase 4: API Layer Tests
# =============================================================================


def _make_sap_csv_bytes(today_str, yesterday_str):
    content = (
        'Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n'
        f'DE01;{today_str};DSL;Diesel;500,00;L;750.00\n'
        f'DE02;{yesterday_str};GAS;Gas;100,00;M3;200.00\n'
    )
    return content.encode('utf-8')


class APILayerTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.org = Organization.objects.create(name='API Test Org')
        self.user = User.objects.create_user(username='api_analyst', password='password')
        # Grant the test user an active Analyst membership so tenant resolution
        # and upload/approve permissions succeed.
        from apps.accounts.models import Membership, Role
        Membership.objects.create(
            user=self.user, organization=self.org, role=Role.ANALYST, active=True
        )
        self.client.force_authenticate(user=self.user)

        self.sap_ds = DataSource.objects.create(
            organization=self.org,
            name='SAP Fuel Feed',
            source_type=DataSource.SourceType.SAP_FUEL,
        )
        self.utility_ds = DataSource.objects.create(
            organization=self.org,
            name='Utility Feed',
            source_type=DataSource.SourceType.UTILITY_ELECTRICITY,
        )
        self.travel_ds = DataSource.objects.create(
            organization=self.org,
            name='Travel Feed',
            source_type=DataSource.SourceType.CORP_TRAVEL,
        )

        today_str = date.today().strftime('%d.%m.%Y')
        yesterday_str = (date.today() - timedelta(days=1)).strftime('%d.%m.%Y')
        self._sap_csv_bytes = _make_sap_csv_bytes(today_str, yesterday_str)

    def _upload_sap(self):
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        file_obj = InMemoryUploadedFile(
            file=BytesIO(self._sap_csv_bytes), field_name='file',
            name='sap_test.csv', content_type='text/csv',
            size=len(self._sap_csv_bytes), charset='utf-8',
        )
        return self.client.post(
            '/api/upload/sap/',
            data={'file': file_obj, 'data_source': str(self.sap_ds.id)},
            format='multipart',
        )

    # Upload tests

    def test_sap_upload_success(self):
        response = self._upload_sap()
        # Phase 5b: upload is now asynchronous — 202 Accepted, not 201. Under
        # CELERY_TASK_ALWAYS_EAGER (the test runner) the task has already
        # fully run by the time this response is built, so the counts below
        # are real, not placeholders — this is not true against a real async
        # worker (see apps.ingestion.tests_tasks.IngestTaskTests).
        self.assertEqual(response.status_code, drf_status.HTTP_202_ACCEPTED)
        data = response.json()
        self.assertEqual(data['status'], UploadBatch.BatchStatus.COMPLETED)
        self.assertEqual(data['total_rows'], 2)
        self.assertEqual(data['failed_rows'], 0)
        # A1: the original uploaded filename is preserved (not the temp name).
        self.assertEqual(data['file_name'], 'sap_test.csv')
        batch = UploadBatch.objects.get(id=data['batch_id'])
        self.assertEqual(batch.file_name, 'sap_test.csv')
        self.assertEqual(EmissionRecord.objects.filter(batch=batch).count(), 2)

    def test_sap_upload_type_mismatch_returns_400(self):
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        file_obj = InMemoryUploadedFile(
            file=BytesIO(self._sap_csv_bytes), field_name='file',
            name='wrong.csv', content_type='text/csv',
            size=len(self._sap_csv_bytes), charset='utf-8',
        )
        response = self.client.post(
            '/api/upload/sap/',
            data={'file': file_obj, 'data_source': str(self.utility_ds.id)},
            format='multipart',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid DataSource type', response.json()['error'])

    def test_sap_upload_no_file_returns_400(self):
        response = self.client.post(
            '/api/upload/sap/',
            data={'data_source': str(self.sap_ds.id)},
            format='multipart',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn('file', response.json())

    def test_sap_upload_empty_file_returns_400(self):
        # D5: an empty file is rejected with a clear validation message.
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        file_obj = InMemoryUploadedFile(
            file=BytesIO(b''), field_name='file',
            name='empty.csv', content_type='text/csv',
            size=0, charset='utf-8',
        )
        response = self.client.post(
            '/api/upload/sap/',
            data={'file': file_obj, 'data_source': str(self.sap_ds.id)},
            format='multipart',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn('empty', str(response.json()).lower())

    def test_travel_upload_success(self):
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        today = date.today().isoformat()
        travel_data = [{
            'trip_id': 'T001', 'travel_mode': 'FLIGHT',
            'origin': 'LHR', 'destination': 'AMS',
            'distance_km': 370.0, 'travel_date': today,
            'employee_id': 'EMP001', 'class': 'ECONOMY',
        }]
        payload = json.dumps(travel_data).encode('utf-8')
        file_obj = InMemoryUploadedFile(
            file=BytesIO(payload), field_name='file',
            name='travel.json', content_type='application/json',
            size=len(payload), charset='utf-8',
        )
        response = self.client.post(
            '/api/upload/travel/',
            data={'file': file_obj, 'data_source': str(self.travel_ds.id)},
            format='multipart',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json()['total_rows'], 1)

    def test_upload_parse_errors_are_structured(self):
        # A2: parser errors must be returned as row-addressable objects
        # ({"row_index", "error"}), not opaque strings.
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        today = date.today().isoformat()
        travel_data = [
            {
                'trip_id': 'T001', 'travel_mode': 'RAIL',
                'origin': 'LON', 'destination': 'PAR',
                'distance_km': 490.0, 'travel_date': today,
                'employee_id': 'EMP001',
            },
            "this-is-not-an-object",  # -> per-record parse error at row_index 2
        ]
        payload = json.dumps(travel_data).encode('utf-8')
        file_obj = InMemoryUploadedFile(
            file=BytesIO(payload), field_name='file',
            name='travel_bad.json', content_type='application/json',
            size=len(payload), charset='utf-8',
        )
        response = self.client.post(
            '/api/upload/travel/',
            data={'file': file_obj, 'data_source': str(self.travel_ds.id)},
            format='multipart',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_202_ACCEPTED)
        data = response.json()
        self.assertTrue(len(data['errors']) >= 1)
        first = data['errors'][0]
        self.assertIsInstance(first, dict)
        self.assertIn('row_index', first)
        self.assertIn('error', first)
        self.assertEqual(first['row_index'], 2)

    # Batch list / detail tests

    def test_batch_list(self):
        self._upload_sap()
        response = self.client.get('/api/batches/')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertGreaterEqual(response.json()['count'], 1)

    def test_batch_detail(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        response = self.client.get(f'/api/batches/{batch_id}/')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertEqual(response.json()['id'], batch_id)
        self.assertEqual(response.json()['total_rows'], 2)

    # Record filtering tests

    def test_records_filter_by_batch(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        response = self.client.get(f'/api/records/?batch={batch_id}')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        records = response.json()['results']
        self.assertEqual(len(records), 2)
        for r in records:
            self.assertEqual(r['batch'], batch_id)

    def test_records_filter_by_status(self):
        self._upload_sap()
        response = self.client.get('/api/records/?status=DRAFT')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        for r in response.json()['results']:
            self.assertEqual(r['status'], 'DRAFT')

    def test_records_filter_suspicious_false(self):
        self._upload_sap()
        response = self.client.get('/api/records/?suspicious=false')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        for r in response.json()['results']:
            self.assertFalse(r['is_suspicious'])

    def test_records_filter_by_data_source(self):
        self._upload_sap()
        response = self.client.get(f'/api/records/?data_source={self.sap_ds.id}')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        self.assertGreaterEqual(response.json()['count'], 1)

    # Approval workflow tests

    def test_approve_record_success(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        record = EmissionRecord.objects.filter(
            batch_id=batch_id, status=EmissionRecord.RecordStatus.DRAFT
        ).first()
        self.assertIsNotNone(record)

        response = self.client.post(
            f'/api/records/{record.id}/approve/',
            data={'reason': 'Analyst confirmed'},
            format='json',
        )
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data['status'], EmissionRecord.RecordStatus.APPROVED)
        self.assertIsNotNone(data['approved_at'])

        audit = AuditTrail.objects.filter(record_uuid_backup=record.id, action='RECORD_APPROVAL').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.changed_by, self.user)
        self.assertEqual(audit.changes['status'][1], EmissionRecord.RecordStatus.APPROVED)

    def test_approve_no_reason_succeeds(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        record = EmissionRecord.objects.filter(batch_id=batch_id).first()
        response = self.client.post(f'/api/records/{record.id}/approve/', data={}, format='json')
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)

    def test_approve_already_approved_returns_400(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        record = EmissionRecord.objects.filter(batch_id=batch_id).first()

        self.client.post(f'/api/records/{record.id}/approve/', data={'reason': 'first'}, format='json')
        response = self.client.post(f'/api/records/{record.id}/approve/', data={'reason': 'second'}, format='json')

        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn('Approved & Audit Locked', response.json()['detail'])

    def test_approve_failed_record_returns_400(self):
        from io import BytesIO
        from django.core.files.uploadedfile import InMemoryUploadedFile
        today_str = date.today().strftime('%d.%m.%Y')
        bad_csv = (
            'Werk;Buchungsdatum;Material;Materialkurztext;Menge;Einheit;Nettopreis\n'
            f'DE01;{today_str};DSL;Diesel;-100,00;L;0.00\n'
        ).encode('utf-8')
        file_obj = InMemoryUploadedFile(
            file=BytesIO(bad_csv), field_name='file',
            name='bad.csv', content_type='text/csv',
            size=len(bad_csv), charset='utf-8',
        )
        self.client.post(
            '/api/upload/sap/',
            data={'file': file_obj, 'data_source': str(self.sap_ds.id)},
            format='multipart',
        )
        failed = EmissionRecord.objects.filter(status=EmissionRecord.RecordStatus.FAILED).first()
        self.assertIsNotNone(failed)

        response = self.client.post(f'/api/records/{failed.id}/approve/', data={}, format='json')
        self.assertEqual(response.status_code, drf_status.HTTP_400_BAD_REQUEST)
        self.assertIn('Failed validation', response.json()['detail'])

    def test_audit_trail_immutable_after_approval(self):
        resp = self._upload_sap()
        batch_id = resp.json()['batch_id']
        record = EmissionRecord.objects.filter(batch_id=batch_id).first()
        self.client.post(f'/api/records/{record.id}/approve/', data={'reason': 'ok'}, format='json')

        audit_log = AuditTrail.objects.get(record_uuid_backup=record.id)
        audit_log.reason = 'Tampered'
        with self.assertRaises(ValidationError):
            audit_log.save()
        with self.assertRaises(ValidationError):
            audit_log.delete()
