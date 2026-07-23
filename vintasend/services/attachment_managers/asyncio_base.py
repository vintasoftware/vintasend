import hashlib
import mimetypes
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from vintasend.services.attachment_managers.base import (
    download_from_url,
    is_url,
    read_file_data,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        AttachmentFile,
        AttachmentFileRecord,
        FileAttachment,
        StorageIdentifiers,
    )


__all__ = [
    "AsyncIOBaseAttachmentManager",
    "download_from_url",
    "is_url",
    "read_file_data",
]


class AsyncIOBaseAttachmentManager(ABC):
    """AsyncIO mirror of `BaseAttachmentManager`.

    `reconstruct_attachment_file` stays synchronous in both: it builds a handle from
    identifiers and performs no I/O, so there is nothing to await.
    """

    @abstractmethod
    async def upload_file(
        self,
        file: "FileAttachment",
        filename: str,
        content_type: str | None = None,
    ) -> "AttachmentFileRecord":
        """Store `file` and return the `AttachmentFileRecord` describing it."""
        ...

    @abstractmethod
    def reconstruct_attachment_file(
        self, storage_identifiers: "StorageIdentifiers"
    ) -> "AttachmentFile":
        """Build a handle to a previously stored file from its `storage_identifiers`.

        This performs no I/O -- it constructs a lazy handle, so it stays synchronous.
        """
        ...

    @abstractmethod
    async def delete_file_by_identifiers(self, storage_identifiers: "StorageIdentifiers") -> None:
        """Delete the stored file referenced by `storage_identifiers`."""
        ...

    def detect_content_type(self, filename: str) -> str:
        """Guess a MIME type from a filename, falling back to `application/octet-stream`."""
        content_type, _ = mimetypes.guess_type(filename)
        return content_type or "application/octet-stream"

    def calculate_checksum(self, data: bytes) -> str:
        """Return the sha256 hex digest of `data`."""
        return hashlib.sha256(data).hexdigest()

    def file_to_bytes(self, file: "FileAttachment") -> bytes:
        """Read `file` into memory, whatever shape it arrived in.

        Accepts raw `bytes`, a local path (`str` or `Path`), a URL `str`, or any
        file-like object with `read()`. This is synchronous rather than a coroutine
        because every read here (local disk, in-memory buffer, or `requests`) is
        already blocking, and there is no async HTTP dependency in this library to
        make a `download_from_url` await meaningful.
        """
        if isinstance(file, bytes):
            return file
        return read_file_data(file)
