"""
Local filesystem StorageService provider (Phase 5b).

Fallback for `manage.py runserver` and unit tests run outside Docker Compose
(where a MinIO container provides the real S3-compatible path — see
docker-compose.yml). Wraps Django's built-in FileSystemStorage.

generate_download_url() still returns a fully-qualified HTTP URL (via
MEDIA_BASE_URL + MEDIA_URL) rather than a filesystem path or bare relative
path — there is no native "presigned URL" concept for a local disk, but the
StorageService contract requires an absolute, directly-usable URL regardless
of provider, so this backend builds one from the configured dev origin.
"""
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from .base import StorageObjectNotFound, StorageService


class LocalFileSystemStorageService(StorageService):
    def __init__(self):
        self._storage = FileSystemStorage(location=settings.MEDIA_ROOT, base_url=settings.MEDIA_URL)

    def save(self, key, file_obj, metadata=None, content_type=None):
        # content_type/metadata have no effect locally — FileSystemStorage
        # doesn't model object metadata the way S3 does. Accepted (per the
        # StorageService contract, metadata support is best-effort) and
        # silently ignored, rather than raising, so callers never need to
        # special-case the backend just because it's running locally.
        data = file_obj.read() if hasattr(file_obj, "read") else file_obj
        return self._storage.save(key, ContentFile(data))

    def open(self, key):
        if not self._storage.exists(key):
            raise StorageObjectNotFound(key)
        return self._storage.open(key, "rb")

    def exists(self, key):
        return self._storage.exists(key)

    def delete(self, key):
        if self._storage.exists(key):
            self._storage.delete(key)

    def generate_download_url(self, key, expires_in=3600):
        # expires_in has no effect locally (accepted for interface parity) —
        # local dev URLs never expire.
        relative = self._storage.url(key)  # e.g. "/media/uploads/.../file.csv"
        return f"{settings.MEDIA_BASE_URL.rstrip('/')}{relative}"
