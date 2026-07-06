import hashlib
import shutil
import tempfile
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.core.storage import StorageObjectNotFound, get_storage_service
from apps.core.storage.providers.local import LocalFileSystemStorageService
from apps.core.storage.providers.s3 import S3StorageService
from apps.core.tasks import ping


class CeleryFoundationTests(TestCase):
    """Phase 5a — Celery app wiring, eager-mode-under-test, worker health probe."""

    def test_ping_task_executes_eagerly_under_test(self):
        # CELERY_TASK_ALWAYS_EAGER is forced True under the test runner (see
        # settings.py `_TESTING` gate) — no broker/worker required.
        result = ping.delay()
        self.assertTrue(result.successful())
        self.assertEqual(result.get(), "pong")

    @override_settings(CELERY_BROKER_URL="")
    def test_healthz_worker_unhealthy_when_broker_not_configured(self):
        response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "unhealthy")
        self.assertIn("not configured", body["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_unhealthy_when_broker_unreachable(self):
        with patch(
            "config.celery.app.control.inspect",
            side_effect=ConnectionError("connection refused"),
        ):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("unreachable", response.json()["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_unhealthy_when_no_workers_respond(self):
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        with patch("config.celery.app.control.inspect", return_value=mock_inspect):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 503)
        self.assertIn("no workers responded", response.json()["detail"])

    @override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
    def test_healthz_worker_ok_when_a_worker_responds(self):
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"celery@worker1": {"ok": "pong"}}
        with patch("config.celery.app.control.inspect", return_value=mock_inspect):
            response = self.client.get("/healthz/worker/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["workers"], ["celery@worker1"])


class StorageServiceFactoryTests(TestCase):
    """Phase 5b — get_storage_service() provider selection."""

    def test_defaults_to_local_backend_under_debug(self):
        # settings.py: STORAGE_BACKEND defaults to 'local' when DEBUG=True,
        # which is how the test runner is invoked.
        self.assertIsInstance(get_storage_service(), LocalFileSystemStorageService)

    @override_settings(
        STORAGE_BACKEND="s3",
        AWS_STORAGE_BUCKET_NAME="test-bucket",
        AWS_ACCESS_KEY_ID="test-key",
        AWS_SECRET_ACCESS_KEY="test-secret",
    )
    def test_selects_s3_backend_when_configured(self):
        self.assertIsInstance(get_storage_service(), S3StorageService)

    @override_settings(STORAGE_BACKEND="azure")
    def test_raises_on_unknown_backend(self):
        with self.assertRaises(ImproperlyConfigured):
            get_storage_service()


class LocalFileSystemStorageServiceTests(TestCase):
    """Phase 5b — the local dev/test provider, exercised through the interface only."""

    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        self.overrides = override_settings(
            MEDIA_ROOT=self.media_root, MEDIA_BASE_URL="http://localhost:8000"
        )
        self.overrides.enable()
        self.addCleanup(self.overrides.disable)
        self.service = LocalFileSystemStorageService()

    def test_save_then_open_roundtrips_bytes(self):
        key = self.service.save("uploads/org1/batch1/sample.csv", BytesIO(b"a,b\n1,2\n"))
        with self.service.open(key) as f:
            self.assertEqual(f.read(), b"a,b\n1,2\n")

    def test_exists_reflects_save_and_delete(self):
        key = "uploads/org1/batch1/sample.csv"
        self.assertFalse(self.service.exists(key))
        self.service.save(key, BytesIO(b"data"))
        self.assertTrue(self.service.exists(key))
        self.service.delete(key)
        self.assertFalse(self.service.exists(key))

    def test_delete_is_idempotent_for_missing_key(self):
        # Must not raise.
        self.service.delete("uploads/does-not-exist.csv")

    def test_open_missing_key_raises_storage_object_not_found(self):
        with self.assertRaises(StorageObjectNotFound):
            self.service.open("uploads/does-not-exist.csv")

    def test_generate_download_url_is_absolute_and_provider_agnostic(self):
        key = self.service.save("uploads/org1/batch1/sample.csv", BytesIO(b"data"))
        url = self.service.generate_download_url(key)
        # Contract: always a fully-qualified HTTP URL, never a filesystem path,
        # regardless of provider — this is what makes it swappable for the S3
        # provider's presigned URL without callers special-casing either one.
        self.assertTrue(url.startswith("http://localhost:8000/media/"))

    def test_save_accepts_and_silently_ignores_metadata(self):
        # Per the StorageService contract: metadata support is best-effort.
        # The local backend cannot persist it (FileSystemStorage has no
        # metadata concept) but must accept the parameter without raising.
        key = self.service.save(
            "uploads/org1/batch1/sample.csv",
            BytesIO(b"data"),
            metadata={"source-system": "sap-feed"},
        )
        with self.service.open(key) as f:
            self.assertEqual(f.read(), b"data")


class S3StorageServiceTests(TestCase):
    """Phase 5b — the S3-compatible provider, verified as a thin delegating
    adapter over django-storages' S3Storage (no real network/AWS calls)."""

    @override_settings(
        STORAGE_BACKEND="s3",
        AWS_STORAGE_BUCKET_NAME="test-bucket",
        AWS_ACCESS_KEY_ID="test-key",
        AWS_SECRET_ACCESS_KEY="test-secret",
        AWS_S3_ENDPOINT_URL="http://minio:9000",
        AWS_S3_ADDRESSING_STYLE="path",
    )
    def setUp(self):
        self.service = S3StorageService()
        self.service._storage = MagicMock()

    def test_save_wraps_bytes_and_sets_content_type(self):
        self.service._storage.save.return_value = "uploads/org1/batch1/sample.csv"
        key = self.service.save(
            "uploads/org1/batch1/sample.csv", BytesIO(b"a,b\n"), content_type="text/csv"
        )
        self.assertEqual(key, "uploads/org1/batch1/sample.csv")
        saved_name, saved_content = self.service._storage.save.call_args[0]
        self.assertEqual(saved_name, "uploads/org1/batch1/sample.csv")
        self.assertEqual(saved_content.read(), b"a,b\n")
        self.assertEqual(saved_content.content_type, "text/csv")

    # SHA-256 of b"data", used to assert the checksum below without
    # depending on hashlib inside the test itself.
    _DATA_SHA256 = "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7"

    def test_save_with_metadata_threads_it_through_pending_attribute_then_clears_it(self):
        # get_object_parameters(name) — django-storages' own per-object
        # customization hook — only receives the object name, not the
        # content, so there's no built-in way to pass metadata through a
        # normal save() call. S3StorageService bridges that gap via a
        # transient _pending_metadata attribute on the storage instance; this
        # verifies OUR side of that contract (set during the call, cleared
        # after), independent of django-storages' own correctness (verified
        # separately against real MinIO, not mockable in a meaningful way).
        captured = {}

        def fake_save(name, content):
            captured["pending_metadata"] = self.service._storage._pending_metadata
            return name

        self.service._storage.save.side_effect = fake_save

        self.service.save(
            "uploads/org1/batch1/sample.csv",
            BytesIO(b"data"),
            metadata={"source-system": "sap-feed", "uploaded-by": "42"},
        )

        # Caller-supplied keys are preserved, and "sha256" is always added —
        # an objective fact about the uploaded bytes, never caller-overridable.
        self.assertEqual(
            captured["pending_metadata"],
            {
                "source-system": "sap-feed",
                "uploaded-by": "42",
                "sha256": self._DATA_SHA256,
            },
        )
        # Cleared afterwards so it never leaks into an unrelated later save().
        self.assertIsNone(self.service._storage._pending_metadata)

    def test_save_without_caller_metadata_still_computes_sha256(self):
        # Checksum groundwork (future duplicate detection / audit integrity /
        # provenance) is computed unconditionally for a provider that can
        # persist metadata — even when the caller passes none at all.
        captured = {}

        def fake_save(name, content):
            captured["pending_metadata"] = self.service._storage._pending_metadata
            return name

        self.service._storage.save.side_effect = fake_save

        self.service.save("uploads/org1/batch1/sample.csv", BytesIO(b"data"))

        self.assertEqual(captured["pending_metadata"], {"sha256": self._DATA_SHA256})
        self.assertIsNone(self.service._storage._pending_metadata)

    def test_save_computes_sha256_from_the_actual_uploaded_bytes(self):
        # A different payload must produce a different (correct) checksum —
        # guards against a hardcoded/stale value slipping through.
        captured = {}

        def fake_save(name, content):
            captured["pending_metadata"] = self.service._storage._pending_metadata
            return name

        self.service._storage.save.side_effect = fake_save

        payload = b"a completely different payload"
        expected = hashlib.sha256(payload).hexdigest()

        self.service.save("uploads/other.csv", BytesIO(payload))

        self.assertEqual(captured["pending_metadata"]["sha256"], expected)
        self.assertNotEqual(expected, self._DATA_SHA256)

    def test_open_missing_key_raises_storage_object_not_found(self):
        # NOTE: deliberately patches our own _object_exists, not
        # self.service._storage.exists — django-storages' S3Storage.exists()
        # is not a general existence check (see s3.py's _object_exists
        # docstring for why relying on it was a real bug caught via a live
        # MinIO round-trip), so tests must exercise the same code path
        # production actually uses.
        with patch.object(self.service, "_object_exists", return_value=False):
            with self.assertRaises(StorageObjectNotFound):
                self.service.open("uploads/does-not-exist.csv")

    def test_open_existing_key_delegates_to_underlying_storage(self):
        with patch.object(self.service, "_object_exists", return_value=True):
            self.service.open("uploads/org1/batch1/sample.csv")
        self.service._storage.open.assert_called_once_with(
            "uploads/org1/batch1/sample.csv", "rb"
        )

    def test_delete_only_calls_underlying_delete_when_object_exists(self):
        with patch.object(self.service, "_object_exists", return_value=False):
            self.service.delete("uploads/does-not-exist.csv")
        self.service._storage.delete.assert_not_called()

        with patch.object(self.service, "_object_exists", return_value=True):
            self.service.delete("uploads/org1/batch1/sample.csv")
        self.service._storage.delete.assert_called_once_with(
            "uploads/org1/batch1/sample.csv"
        )

    def test_generate_download_url_delegates_with_requested_expiry(self):
        self.service._storage.url.return_value = "https://minio.example/presigned?sig=abc"
        url = self.service.generate_download_url("uploads/sample.csv", expires_in=120)
        self.assertEqual(self.service._storage.querystring_expire, 120)
        self.assertEqual(url, "https://minio.example/presigned?sig=abc")
