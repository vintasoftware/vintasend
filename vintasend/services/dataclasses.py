import datetime
import io
import mimetypes
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TypedDict


# Type alias for supported file inputs (for creating notifications)
FileAttachment = (
    BinaryIO |           # File-like object with read()
    io.BytesIO |         # In-memory bytes
    io.StringIO |        # In-memory text
    Path |               # Path object
    str |                # File path string OR URL
    bytes                # Raw bytes data
)


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
        return content_type or 'application/octet-stream'

    def is_url(self) -> bool:
        """Check if file is a URL"""
        return isinstance(self.file, str) and (
            self.file.startswith('http://') or
            self.file.startswith('https://') or
            self.file.startswith('s3://') or
            self.file.startswith('gs://') or
            self.file.startswith('azure://')
        )


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
    # Backend-specific storage metadata
    storage_metadata: dict[str, Any] = field(default_factory=dict)

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

class UpdateNotificationKwargs(TypedDict, total=False):
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str | None
    preheader_template: str | None
    adapter_extra_parameters: dict | None
    attachments: list[StoredAttachment]
