import datetime
import uuid
from typing import Generic, TypeVar

from vintasend.constants import NotificationTypes
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_adapters.async_base import (
    AsyncBaseNotificationAdapter,
    NotificationDict,
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
    sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))


BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class FakeAsyncIOEmailAdapter(Generic[BAIO, T], AsyncIOBaseNotificationAdapter[BAIO, T]):
    notification_type = NotificationTypes.EMAIL
    backend: BAIO
    template_renderer: T
    sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails = []

    async def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))


class FakeAsyncEmailAdapter(AsyncBaseNotificationAdapter, Generic[B, T], FakeEmailAdapter[B, T]):
    notification_type = NotificationTypes.EMAIL

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        pass

    def delayed_send(self, notification_dict: NotificationDict, context_dict: dict) -> None:
        notification = self.notification_from_dict(notification_dict)
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
        )


class InvalidAdapter:
    def __init__(self, *_args, **_kwargs):
        pass
