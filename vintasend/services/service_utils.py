import inspect
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Coroutine, TypeGuard

from vintasend.exceptions import InvalidOneOffNotificationRecipientError


if TYPE_CHECKING:
    import io
    from pathlib import Path
    from typing import BinaryIO

    from vintasend.services.dataclasses import NotificationAttachment, NotificationContextDict

    # Everything FileAttachment allows except `bytes`, which read_file_data rejects.
    ReadableFileAttachment = BinaryIO | io.BytesIO | io.StringIO | Path | str


# These mirror the TS patterns `^.+@.+\..+$` and `^\+?[0-9]{10,15}$`, but unanchored: matching
# is done with `fullmatch` instead of `match` + `^`/`$`, because Python's `$` also matches
# immediately before a trailing newline while JS's `$` (no `m` flag) does not. Using `match`
# with `^...$` here would let a trailing "\n" through undetected, unlike the reference behavior.
_EMAIL_PATTERN = re.compile(r".+@.+\..+")
_PHONE_PATTERN = re.compile(r"\+?[0-9]{10,15}")


def validate_email_or_phone(email_or_phone: str) -> None:
    """Validate that email_or_phone is a non-empty, properly formatted email or phone number."""
    if not email_or_phone.strip():
        raise InvalidOneOffNotificationRecipientError(
            "email_or_phone must not be empty or whitespace-only"
        )

    if not (_EMAIL_PATTERN.fullmatch(email_or_phone) or _PHONE_PATTERN.fullmatch(email_or_phone)):
        raise InvalidOneOffNotificationRecipientError(
            "email_or_phone must be a valid email or phone number"
        )


def validate_attachments(
    attachments: list["NotificationAttachment"],
) -> list["NotificationAttachment"]:
    """Validate attachments and return the validated list."""
    # Attachments pass through unchanged for now; no validation is applied yet.
    for attachment in attachments:
        if attachment.is_url():
            # Nothing is validated yet.
            pass

    return attachments


def read_file_data(file: "ReadableFileAttachment") -> bytes:
    """Read file data from a path, URL, `Path` object, or file-like object."""
    from pathlib import Path

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
        raise ValueError(f"Unsupported file type: {type(file)}")


def is_url(file_str: str) -> bool:
    """Check whether a string is a URL rather than a local file path."""
    return file_str.startswith(("http://", "https://", "s3://", "gs://", "azure://"))


def download_from_url(url: str) -> bytes:
    """Download file content from a URL."""
    try:
        import requests
    except ImportError as e:
        raise ImportError("requests library is required to download files from URLs") from e

    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.content


def is_asyncio_context_function(
    context_function: Callable[[Any], "NotificationContextDict"]
    | Callable[[Any], Coroutine[Any, Any, "NotificationContextDict"]],
) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, "NotificationContextDict"]]]:
    return inspect.iscoroutinefunction(context_function)


def is_sync_context_function(
    context_function: Callable[[Any], "NotificationContextDict"]
    | Callable[[Any], Coroutine[Any, Any, "NotificationContextDict"]],
) -> TypeGuard[Callable[[Any], "NotificationContextDict"]]:
    return not inspect.iscoroutinefunction(context_function)
