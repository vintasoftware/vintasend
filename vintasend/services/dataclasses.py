import datetime
import io
import mimetypes
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TypedDict, TypeGuard


# Type alias for supported file inputs (for creating notifications)
FileAttachment = (
    BinaryIO  # File-like object with read()
    | io.BytesIO  # In-memory bytes
    | io.StringIO  # In-memory text
    | Path  # Path object
    | str  # File path string OR URL
    | bytes  # Raw bytes data
)

# Opaque, manager-defined identifiers that let a backend hand a stored file back to
# whichever attachment manager was injected, without ever parsing the contents itself.
# Must carry a non-empty "id"; every other key is manager-defined.
StorageIdentifiers = dict[str, Any]


class AttachmentFile(ABC):
    """Abstract interface for accessing stored attachment files"""

    @abstractmethod
    def read(self) -> bytes:
        """Read the entire file content"""
        pass

    @abstractmethod
    def stream(self) -> BinaryIO:
        """Get a stream for reading the file"""
        pass

    @abstractmethod
    def url(self, expires_in: int = 3600) -> str:
        """Get a temporary URL for file access"""
        pass

    @abstractmethod
    def delete(self) -> None:
        """Delete the file from storage"""
        pass


@dataclass
class NotificationAttachment:
    """Input attachment for creating notifications"""

    file: FileAttachment
    filename: str
    content_type: str | None = None  # Auto-detected if None
    description: str | None = None
    is_inline: bool = False

    def __post_init__(self):
        if self.content_type is None:
            self.content_type = self._detect_content_type()

    def _detect_content_type(self) -> str:
        content_type, _ = mimetypes.guess_type(self.filename)
        return content_type or "application/octet-stream"

    def is_url(self) -> bool:
        """Check if file is a URL"""
        return isinstance(self.file, str) and (
            self.file.startswith("http://")
            or self.file.startswith("https://")
            or self.file.startswith("s3://")
            or self.file.startswith("gs://")
            or self.file.startswith("azure://")
        )


@dataclass
class NotificationAttachmentReference:
    """Attach an already-uploaded file by id instead of re-uploading it.

    ``file_id`` points at an existing `AttachmentFileRecord`. `is_inline` lives here
    rather than on the record because inline-ness is a property of how *this*
    notification uses the file, not of the file itself.
    """

    file_id: str
    description: str | None = None
    is_inline: bool = False


# Union of every input shape a caller may pass when attaching a file to a notification:
# an upload (`NotificationAttachment`) or a reference to an already-stored file by id.
AnyNotificationAttachment = NotificationAttachment | NotificationAttachmentReference


def is_attachment_reference(
    attachment: AnyNotificationAttachment,
) -> TypeGuard[NotificationAttachmentReference]:
    """Type guard distinguishing a by-id reference from an upload."""
    return isinstance(attachment, NotificationAttachmentReference)


@dataclass
class AttachmentFileRecord:
    """A checksum-indexed, stored blob. One record can back many notifications."""

    id: str
    filename: str
    content_type: str
    size: int
    checksum: str  # sha256 hex
    created_at: datetime.datetime
    updated_at: datetime.datetime
    storage_identifiers: StorageIdentifiers


@dataclass
class StoredAttachment:
    """Represents an attachment stored by the backend"""

    id: str | uuid.UUID
    filename: str
    content_type: str
    size: int
    checksum: str
    created_at: datetime.datetime
    file: AttachmentFile  # File access - abstracted through AttachmentFile interface
    description: str | None = None
    is_inline: bool = False
    # The `AttachmentFileRecord` this join row points at; `id` above remains the
    # join row's own id.
    file_id: str = ""
    # Opaque identifiers handed back to the injected attachment manager to reconstruct
    # or delete the underlying file.
    storage_identifiers: StorageIdentifiers = field(default_factory=dict)
    # Deprecated alias for storage_identifiers, kept for backwards compatibility.
    # Accepting it as a real field keeps StoredAttachment(storage_metadata=...) working.
    storage_metadata: StorageIdentifiers = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Reconcile the deprecated alias with the canonical field: a caller may pass
        # either one. storage_identifiers wins when both are given.
        if not self.storage_identifiers and self.storage_metadata:
            self.storage_identifiers = self.storage_metadata
        self.storage_metadata = self.storage_identifiers

    def get_file_data(self) -> bytes:
        """Get the raw file data"""
        return self.file.read()

    def get_file_stream(self) -> BinaryIO:
        """Get a stream for reading the file (for large files)"""
        return self.file.stream()

    def get_file_url(self, expires_in: int = 3600) -> str:
        """Get a temporary URL for file access (if supported by backend)"""
        return self.file.url(expires_in)

    def delete(self) -> None:
        """Delete this attachment from storage"""
        self.file.delete()


class NotificationContextDict(dict):
    """
    A dictionary that only accepts string keys and values of types: int, float, str,
    list[NotificationContextDict], and dict[str, NotificationContextDict].
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.update(*args, **kwargs)

    def update(self, *args, **kwargs):
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

    def __setitem__(
        self,
        key: str,
        value: int
        | float
        | str
        | list["NotificationContextDict"]
        | dict[str, "NotificationContextDict"],
    ):
        if not isinstance(key, str):
            raise TypeError("Keys must be strings")
        if not isinstance(
            value,
            (int | float | str | list | dict),
        ):
            raise TypeError("Value must be an int, float, str, list, or dict")
        if isinstance(value, list):
            value = [self._validate_list_item(item) for item in value]
        if isinstance(value, dict):
            value = {k: self._validate_dict_value(v) for k, v in value.items()}
        super().__setitem__(key, value)

    def _validate_list_item(self, item):
        if not isinstance(item, NotificationContextDict):
            raise TypeError("List items must be SerializableDict instances")
        return item

    def _validate_dict_value(self, value):
        if not isinstance(value, NotificationContextDict):
            raise TypeError("Dict values must be SerializableDict instances")
        return value

    def copy(self) -> "NotificationContextDict":
        return self.__class__(super().copy())


@dataclass
class Notification:
    id: int | str | uuid.UUID  # noqa: A003
    user_id: int | str | uuid.UUID
    notification_type: str
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str
    preheader_template: str
    status: str
    context_used: dict | None = None
    adapter_used: str | None = None
    adapter_extra_parameters: dict | None = None
    attachments: list[StoredAttachment] = field(default_factory=list)
    created: datetime.datetime | None = None
    modified: datetime.datetime | None = None


@dataclass
class OneOffNotification:
    id: int | str | uuid.UUID  # noqa: A003
    email_or_phone: str
    first_name: str
    last_name: str
    notification_type: str
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str
    preheader_template: str
    status: str
    context_used: dict | None = None
    adapter_used: str | None = None
    adapter_extra_parameters: dict | None = None
    attachments: list[StoredAttachment] = field(default_factory=list)
    created: datetime.datetime | None = None
    modified: datetime.datetime | None = None


class UpdateNotificationKwargs(TypedDict, total=False):
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str | None
    preheader_template: str | None
    adapter_extra_parameters: dict | None
    # This stays `StoredAttachment`, not `AnyNotificationAttachment`. The plan's Open Questions
    # table suggested widening it. We did not do that. `persist_notification_update` has no upload
    # path. It just does `setattr` for every field. A raw attachment passed here would be saved as
    # if it were already stored, which is wrong. Revisit this if an update-side upload flow is
    # ever added.
    attachments: list[StoredAttachment]
