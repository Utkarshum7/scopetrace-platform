"""
StorageService provider selection (Phase 5b).

Adding a new provider (Azure, GCS, a second MinIO-style deployment, etc.) is
additive: drop a new class under providers/, add one branch here. Nothing
else in this module, or in any caller, changes.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import StorageService


def get_storage_service() -> StorageService:
    """Construct the configured StorageService provider.

    Always builds fresh from current settings (no caching) so
    `override_settings` in tests takes effect without extra plumbing —
    construction itself does no network I/O, so this is cheap.
    """
    backend = settings.STORAGE_BACKEND

    if backend == "s3":
        from .providers.s3 import S3StorageService

        return S3StorageService()

    if backend == "local":
        from .providers.local import LocalFileSystemStorageService

        return LocalFileSystemStorageService()

    raise ImproperlyConfigured(f"Unknown STORAGE_BACKEND: {backend!r}")
