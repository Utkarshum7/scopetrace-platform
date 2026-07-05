"""
StorageService — the provider-independent durable file storage contract.

Ingestion (and any future feature needing durable file storage) depends only
on this interface, never on a concrete provider. Swapping providers — S3 ->
Cloudflare R2 -> Backblaze B2 -> Google Cloud Storage -> Azure Blob -> local
dev — is a settings change plus, at most, one new thin adapter class; it is
never a change to caller code.
"""
from abc import ABC, abstractmethod
from typing import IO, Optional


class StorageError(Exception):
    """Base class for all storage-layer failures, deliberately provider-agnostic.
    Callers catch this (or StorageObjectNotFound), never a provider SDK's
    native exception type (e.g. botocore.exceptions.ClientError) — catching
    the SDK's exception would leak the provider straight back through."""


class StorageObjectNotFound(StorageError):
    """Raised by open() when `key` does not exist."""


class StorageService(ABC):
    @abstractmethod
    def save(self, key: str, file_obj: IO[bytes], content_type: Optional[str] = None) -> str:
        """Persist `file_obj` durably under `key`.

        Returns the key actually stored under (a provider may sanitize or
        namespace it; callers should persist the returned key, not assume
        it equals the input).
        """

    @abstractmethod
    def open(self, key: str) -> IO[bytes]:
        """Return a binary file-like object for reading.

        Raises StorageObjectNotFound if `key` does not exist.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if an object exists at `key`."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object at `key`. Idempotent — deleting a missing key is
        not an error."""

    @abstractmethod
    def generate_download_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a fully-qualified, time-limited HTTP(S) URL for downloading
        `key` directly — always a real URL, never a filesystem path, and
        never requiring a further storage-layer call to use. This contract is
        identical across every provider, including local development, so
        callers never special-case the backend.
        """
