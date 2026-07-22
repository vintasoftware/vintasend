import inspect
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Coroutine, TypeGuard

from vintasend.exceptions import InvalidOneOffNotificationRecipientError, NotificationError
from vintasend.services.dataclasses import NotificationAttachment, is_attachment_reference


if TYPE_CHECKING:
    from vintasend.services.dataclasses import AnyNotificationAttachment, NotificationContextDict


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
    attachments: list["AnyNotificationAttachment"],
) -> list["AnyNotificationAttachment"]:
    """Validate attachment inputs and return the validated list unchanged.

    Each entry must be either a ``NotificationAttachment`` upload or a
    ``NotificationAttachmentReference`` by-id reference; a reference must carry a
    non-empty ``file_id``. Anything else raises ``NotificationError`` rather than failing
    obscurely deeper in the persist flow.
    """
    for attachment in attachments:
        if is_attachment_reference(attachment):
            if not attachment.file_id:
                raise NotificationError("Attachment reference must carry a non-empty file_id")
        elif not isinstance(attachment, NotificationAttachment):
            raise NotificationError(f"Unsupported attachment type: {type(attachment).__name__}")

    return attachments


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
