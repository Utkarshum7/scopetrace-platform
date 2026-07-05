"""
S3-compatible StorageService provider (Phase 5b).

Works against real AWS S3 and any S3-compatible object store (Cloudflare R2,
Backblaze B2, MinIO — used for local Docker Compose) by changing only
AWS_S3_ENDPOINT_URL / AWS_S3_ADDRESSING_STYLE. Built on django-storages'
S3Storage, which already handles multipart upload, retries, and presigned
URL signing correctly — this class stays a thin adapter, never a boto3
reimplementation, so the correctness burden stays on a well-tested library.
"""
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.files.base import ContentFile
from storages.backends.s3 import S3Storage
from storages.utils import clean_name

from .base import StorageObjectNotFound, StorageService


class S3StorageService(StorageService):
    def __init__(self):
        self._storage = S3Storage(
            bucket_name=settings.AWS_STORAGE_BUCKET_NAME,
            access_key=settings.AWS_ACCESS_KEY_ID,
            secret_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
            endpoint_url=settings.AWS_S3_ENDPOINT_URL or None,
            addressing_style=settings.AWS_S3_ADDRESSING_STYLE,
            querystring_auth=True,
            querystring_expire=settings.AWS_QUERYSTRING_EXPIRE,
            default_acl=None,
        )

    def _object_exists(self, key: str) -> bool:
        # IMPORTANT: django-storages' own S3Storage.exists() is NOT a general
        # existence check. With AWS_S3_FILE_OVERWRITE=True (the default, and
        # what we want — an idempotent task retry re-saving the same key
        # should overwrite cleanly, not get auto-renamed), it is hard-coded to
        # always return False: it exists purely as a Django Storage.save()
        # collision-avoidance hook, inverted so save() never renames on
        # overwrite. Using it for exists()/open()/delete() would make every
        # object look "missing" regardless of whether it's actually there
        # (found via a real MinIO round-trip, not just reading — a save()
        # immediately followed by exists() returned False before this fix).
        # We query S3 directly instead.
        client = self._storage.connection.meta.client
        object_key = self._storage._normalize_name(clean_name(key))
        try:
            client.head_object(Bucket=self._storage.bucket_name, Key=object_key)
            return True
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise

    def save(self, key, file_obj, content_type=None):
        data = file_obj.read() if hasattr(file_obj, "read") else file_obj
        wrapped = ContentFile(data)
        if content_type:
            # django-storages reads this attribute (mirroring Django's
            # UploadedFile) to set the object's Content-Type header.
            wrapped.content_type = content_type
        return self._storage.save(key, wrapped)

    def open(self, key):
        if not self._object_exists(key):
            raise StorageObjectNotFound(key)
        return self._storage.open(key, "rb")

    def exists(self, key):
        return self._object_exists(key)

    def delete(self, key):
        if self._object_exists(key):
            self._storage.delete(key)

    def generate_download_url(self, key, expires_in=3600):
        # querystring_expire controls the presigned URL's TTL on the next
        # .url() call — set it per-call so callers can request a shorter or
        # longer expiry than the configured default.
        self._storage.querystring_expire = expires_in
        return self._storage.url(key)
