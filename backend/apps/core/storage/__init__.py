"""
StorageService — provider-independent durable file storage (Phase 5b).

Callers must use `get_storage_service()` and the `StorageService` interface
exclusively. Never import a concrete provider (S3StorageService,
LocalFileSystemStorageService) or a provider SDK (boto3, django-storages)
outside this package — that is precisely the coupling this abstraction
exists to prevent. See base.py for the interface contract.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import StorageError, StorageObjectNotFound, StorageService

__all__ = ["StorageService", "StorageError", "StorageObjectNotFound", "get_storage_service"]


def get_storage_service() -> StorageService:
    """Construct the configured StorageService provider.

    Always builds fresh from current settings (no caching) so
    `override_settings` in tests takes effect without extra plumbing —
    construction itself does no network I/O, so this is cheap.
    """
    backend = settings.STORAGE_BACKEND

    if backend == "s3":
        from .s3 import S3StorageService

        return S3StorageService()

    if backend == "local":
        from .local import LocalFileSystemStorageService

        return LocalFileSystemStorageService()

    raise ImproperlyConfigured(f"Unknown STORAGE_BACKEND: {backend!r}")
