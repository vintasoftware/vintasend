import hashlib
import io
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from vintasend.exceptions import UnsupportedAttachmentFileTypeError


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        AttachmentFile,
        AttachmentFileRecord,
        FileAttachment,
        StorageIdentifiers,
    )

    # Everything FileAttachment allows except `bytes`, which read_file_data rejects.
    ReadableFileAttachment = BinaryIO | io.BytesIO | io.StringIO | Path | str


# Schemes recognized as remote locations rather than local file paths. Mirrors the
# check `NotificationAttachment.is_url` performs on the input side.
_URL_SCHEMES = ("http://", "https://", "s3://", "gs://", "azure://")


def is_url(file_str: str) -> bool:
    """Check whether a string is a URL rather than a local file path."""
    return file_str.startswith(_URL_SCHEMES)


def download_from_url(url: str) -> bytes:
    """Download file content from a URL.

    This stays a plain, blocking call even on the AsyncIO manager: `requests` is the
    library's only HTTP dependency and has no async mode, and adding one is out of
    scope for this seam.
    """
    try:
        import requests
    except ImportError as e:
        raise ImportError("requests library is required to download files from URLs") from e

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def read_file_data(file: "ReadableFileAttachment") -> bytes:
    """Read file data from a path, URL, `Path` object, or file-like object.

    Unlike `BaseAttachmentManager.file_to_bytes`, this rejects raw `bytes` input -- there
    is nothing to "read" from bytes already in memory. `file_to_bytes` special-cases
    `bytes` before falling back to this.
    """
    if isinstance(file, str):
        if is_url(file):
            return download_from_url(file)
        else:
            with open(file, "rb") as f:
                return f.read()
    elif isinstance(file, Path):
        with open(file, "rb") as f:
            return f.read()
    elif hasattr(file, "read"):
        current_pos = file.tell() if hasattr(file, "tell") else 0
        if hasattr(file, "seek"):
            file.seek(0)
        data = file.read()
        if hasattr(file, "seek"):
            file.seek(current_pos)
        if isinstance(data, str):
            return data.encode("utf-8")
        return data
    else:
        raise UnsupportedAttachmentFileTypeError(f"Unsupported file type: {type(file)}")


class BaseAttachmentManager(ABC):
    """Owns every byte of attachment storage: uploading, reconstructing a handle to a
    stored file, and deleting it.

    A `BaseNotificationBackend` never reads, writes, or downloads a byte itself -- it
    persists rows and hands `StorageIdentifiers` back to whichever manager was injected.
    """

    @abstractmethod
    def upload_file(
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

        This performs no I/O -- it constructs a lazy handle, so it stays synchronous
        even on `AsyncIOBaseAttachmentManager`.
        """
        ...

    @abstractmethod
    def delete_file_by_identifiers(self, storage_identifiers: "StorageIdentifiers") -> None:
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
        file-like object with `read()`.
        """
        if isinstance(file, bytes):
            return file
        return read_file_data(file)
