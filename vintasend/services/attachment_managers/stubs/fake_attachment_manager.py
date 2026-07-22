import datetime
import io
import uuid
from typing import BinaryIO

from vintasend.exceptions import UnsupportedAttachmentFileTypeError
from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
from vintasend.services.attachment_managers.base import BaseAttachmentManager
from vintasend.services.dataclasses import (
    AttachmentFile,
    AttachmentFileRecord,
    FileAttachment,
    StorageIdentifiers,
)


class FakeStoredFile(AttachmentFile):
    """In-memory handle to a file stored by `FakeAttachmentManager`."""

    def __init__(self, storage: dict[str, bytes], file_id: str):
        self._storage = storage
        self._file_id = file_id

    def read(self) -> bytes:
        try:
            return self._storage[self._file_id]
        except KeyError as e:
            raise FileNotFoundError(f"No file stored for id {self._file_id!r}") from e

    def stream(self) -> BinaryIO:
        return io.BytesIO(self.read())

    def url(self, expires_in: int = 3600) -> str:
        return f"fake://attachments/{self._file_id}?expires_in={expires_in}"

    def delete(self) -> None:
        self._storage.pop(self._file_id, None)


class FakeAttachmentManager(BaseAttachmentManager):
    """In-memory attachment manager, sync half. Reference implementation for tests
    and for downstream authors writing a real one."""

    def __init__(self) -> None:
        self._storage: dict[str, bytes] = {}

    def upload_file(
        self,
        file: FileAttachment,
        filename: str,
        content_type: str | None = None,
    ) -> AttachmentFileRecord:
        data = self.file_to_bytes(file)
        file_id = str(uuid.uuid4())
        self._storage[file_id] = data
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return AttachmentFileRecord(
            id=file_id,
            filename=filename,
            content_type=content_type or self.detect_content_type(filename),
            size=len(data),
            checksum=self.calculate_checksum(data),
            created_at=now,
            updated_at=now,
            storage_identifiers={"id": file_id},
        )

    def reconstruct_attachment_file(
        self, storage_identifiers: StorageIdentifiers
    ) -> AttachmentFile:
        file_id = storage_identifiers.get("id")
        if not file_id:
            raise UnsupportedAttachmentFileTypeError(
                "storage_identifiers must carry a non-empty 'id'"
            )
        return FakeStoredFile(self._storage, file_id)

    def delete_file_by_identifiers(self, storage_identifiers: StorageIdentifiers) -> None:
        file_id = storage_identifiers.get("id")
        if file_id:
            self._storage.pop(file_id, None)


class FakeAsyncIOAttachmentManager(AsyncIOBaseAttachmentManager):
    """In-memory attachment manager, AsyncIO half. Reference implementation for tests
    and for downstream authors writing a real one."""

    def __init__(self) -> None:
        self._storage: dict[str, bytes] = {}

    async def upload_file(
        self,
        file: FileAttachment,
        filename: str,
        content_type: str | None = None,
    ) -> AttachmentFileRecord:
        data = self.file_to_bytes(file)
        file_id = str(uuid.uuid4())
        self._storage[file_id] = data
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return AttachmentFileRecord(
            id=file_id,
            filename=filename,
            content_type=content_type or self.detect_content_type(filename),
            size=len(data),
            checksum=self.calculate_checksum(data),
            created_at=now,
            updated_at=now,
            storage_identifiers={"id": file_id},
        )

    def reconstruct_attachment_file(
        self, storage_identifiers: StorageIdentifiers
    ) -> AttachmentFile:
        file_id = storage_identifiers.get("id")
        if not file_id:
            raise UnsupportedAttachmentFileTypeError(
                "storage_identifiers must carry a non-empty 'id'"
            )
        return FakeStoredFile(self._storage, file_id)

    async def delete_file_by_identifiers(self, storage_identifiers: StorageIdentifiers) -> None:
        file_id = storage_identifiers.get("id")
        if file_id:
            self._storage.pop(file_id, None)
