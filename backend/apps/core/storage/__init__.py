"""
StorageService — provider-independent durable file storage (Phase 5b).

Callers must use `get_storage_service()` and the `StorageService` interface
exclusively. Never import a concrete provider (S3StorageService,
LocalFileSystemStorageService) or a provider SDK (boto3, django-storages)
outside this package — that is precisely the coupling this abstraction
exists to prevent.

Package layout:
    base.py           StorageService — the interface itself
    exceptions.py      StorageError, StorageObjectNotFound
    factory.py          get_storage_service() — provider selection
    providers/          concrete providers (local.py, s3.py, ...). Adding a
                        provider (Azure, GCS, a distinct MinIO deployment) is
                        additive — a new file here plus one branch in
                        factory.py, never a change to base.py or callers.
"""
from .base import StorageService
from .exceptions import StorageError, StorageObjectNotFound
from .factory import get_storage_service

__all__ = ["StorageService", "StorageError", "StorageObjectNotFound", "get_storage_service"]
