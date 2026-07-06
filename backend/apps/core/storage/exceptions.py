"""
StorageService exceptions — kept in their own module so provider
implementations (apps/core/storage/providers/*) and the interface
(apps/core/storage/base.py) can both depend on them without a circular
import, and so a future caller can `except` these without importing the
interface module at all.
"""


class StorageError(Exception):
    """Base class for all storage-layer failures, deliberately provider-agnostic.
    Callers catch this (or StorageObjectNotFound), never a provider SDK's
    native exception type (e.g. botocore.exceptions.ClientError) — catching
    the SDK's exception would leak the provider straight back through."""


class StorageObjectNotFound(StorageError):
    """Raised by open() when `key` does not exist."""
