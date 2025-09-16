import datetime
import uuid
from typing import Generic, TypeVar

from vintasend.constants import NotificationTypes
from vintasend.services.dataclasses import Notification, NotificationContextDict, OneOffNotification
from vintasend.services.notification_adapters.async_base import (
    AsyncBaseNotificationAdapter,
    NotificationDict,
    OneOffNotificationDict,
)
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
)


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseTemplatedEmailRenderer)


class FakeEmailAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    notification_type = NotificationTypes.EMAIL
    backend: B
    template_renderer: T

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[tuple["Notification | OneOffNotification", "NotificationContextDict", list[dict]]] = []

    def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)

        # Capture attachment information for testing
        attachment_info = []
        for attachment in notification.attachments:
            attachment_info.append({
                'id': str(attachment.id),
                'filename': attachment.filename,
                'content_type': attachment.content_type,
                'size': attachment.size,
                'is_inline': attachment.is_inline,
                'description': attachment.description,
                'checksum': attachment.checksum,
            })

        self.sent_emails.append((notification, context, attachment_info))


BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class FakeAsyncIOEmailAdapter(Generic[BAIO, T], AsyncIOBaseNotificationAdapter[BAIO, T]):
    notification_type = NotificationTypes.EMAIL
    backend: BAIO
    template_renderer: T

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[tuple["Notification | OneOffNotification", "NotificationContextDict", list[dict]]] = []

    async def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)

        # Capture attachment information for testing
        attachment_info = []
        for attachment in notification.attachments:
            attachment_info.append({
                'id': str(attachment.id),
                'filename': attachment.filename,
                'content_type': attachment.content_type,
                'size': attachment.size,
                'is_inline': attachment.is_inline,
                'description': attachment.description,
                'checksum': attachment.checksum,
            })

        self.sent_emails.append((notification, context, attachment_info))


class FakeAsyncEmailAdapter(AsyncBaseNotificationAdapter, Generic[B, T], FakeEmailAdapter[B, T]):
    notification_type = NotificationTypes.EMAIL

    def send(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict") -> None:
        pass

    def delayed_send(self, notification_dict: NotificationDict | OneOffNotificationDict, context_dict: dict) -> None:
        notification: "Notification | OneOffNotification"
        if "email_or_phone" in notification_dict:
            # This is a OneOffNotificationDict
            one_off_dict: OneOffNotificationDict = notification_dict  # type: ignore
            notification = self.one_off_notification_from_dict(one_off_dict)
        else:
            # This is a NotificationDict
            regular_dict: NotificationDict = notification_dict  # type: ignore
            notification = self.notification_from_dict(regular_dict)
        context = NotificationContextDict(**context_dict)
        super().send(notification, context)

    def _convert_to_uuid(self, value: str) -> uuid.UUID | str:
        try:
            return uuid.UUID(value)
        except ValueError:
            return value

    def notification_from_dict(self, notification_dict: NotificationDict) -> "Notification":
        send_after = (
            datetime.datetime.fromisoformat(notification_dict["send_after"])
            if notification_dict["send_after"]
            else None
        )
        return Notification(
            id=(
                self._convert_to_uuid(notification_dict["id"])
                if isinstance(notification_dict["id"], str)
                else notification_dict["id"]
            ),
            user_id=(
                self._convert_to_uuid(notification_dict["user_id"])
                if isinstance(notification_dict["user_id"], str)
                else notification_dict["user_id"]
            ),
            context_kwargs={
                key: self._convert_to_uuid(value) if isinstance(value, str) else value
                for key, value in notification_dict["context_kwargs"].items()
            },
            send_after=send_after,
            status=notification_dict["status"],
            context_used=notification_dict["context_used"],
            notification_type=notification_dict["notification_type"],
            title=notification_dict["title"],
            body_template=notification_dict["body_template"],
            context_name=notification_dict["context_name"],
            subject_template=notification_dict["subject_template"],
            preheader_template=notification_dict["preheader_template"],
            attachments=[],  # Default to empty attachments for delayed sending
        )

    def one_off_notification_from_dict(self, notification_dict: OneOffNotificationDict) -> "OneOffNotification":
        send_after = (
            datetime.datetime.fromisoformat(notification_dict["send_after"])
            if notification_dict["send_after"]
            else None
        )
        return OneOffNotification(
            id=(
                self._convert_to_uuid(notification_dict["id"])
                if isinstance(notification_dict["id"], str)
                else notification_dict["id"]
            ),
            email_or_phone=notification_dict["email_or_phone"],
            first_name=notification_dict["first_name"],
            last_name=notification_dict["last_name"],
            context_kwargs={
                key: self._convert_to_uuid(value) if isinstance(value, str) else value
                for key, value in notification_dict["context_kwargs"].items()
            },
            send_after=send_after,
            status=notification_dict["status"],
            context_used=notification_dict["context_used"],
            notification_type=notification_dict["notification_type"],
            title=notification_dict["title"],
            body_template=notification_dict["body_template"],
            context_name=notification_dict["context_name"],
            subject_template=notification_dict["subject_template"],
            preheader_template=notification_dict["preheader_template"],
            attachments=[],  # Default to empty attachments for delayed sending
        )


class InvalidAdapter:
    def __init__(self, *_args, **_kwargs):
        pass
