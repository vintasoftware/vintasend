import uuid
from typing import TYPE_CHECKING, Generic, TypeVar

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseTemplatedEmailRenderer)


class FakeEmailAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    notification_type = NotificationTypes.EMAIL
    backend: B
    template_renderer: T

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[
            tuple["Notification | OneOffNotification", "NotificationContextDict", list[dict]]
        ] = []

    def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        self.template_renderer.render(notification, context)

        # Capture attachment information for testing
        attachment_info = [
            {
                "id": str(attachment.id),
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "is_inline": attachment.is_inline,
                "description": attachment.description,
                "checksum": attachment.checksum,
            }
            for attachment in notification.attachments
        ]

        self.sent_emails.append((notification, context, attachment_info))


BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class FakeAsyncIOEmailAdapter(Generic[BAIO, T], AsyncIOBaseNotificationAdapter[BAIO, T]):
    notification_type = NotificationTypes.EMAIL
    backend: BAIO
    template_renderer: T

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[
            tuple["Notification | OneOffNotification", "NotificationContextDict", list[dict]]
        ] = []

    async def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        self.template_renderer.render(notification, context)

        # Capture attachment information for testing
        attachment_info = [
            {
                "id": str(attachment.id),
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size": attachment.size,
                "is_inline": attachment.is_inline,
                "description": attachment.description,
                "checksum": attachment.checksum,
            }
            for attachment in notification.attachments
        ]

        self.sent_emails.append((notification, context, attachment_info))


class FakeAsyncEmailAdapter(AsyncBaseNotificationAdapter, Generic[B, T], FakeEmailAdapter[B, T]):
    """Background email adapter double.

    Delivery happens through the inherited `FakeEmailAdapter.send`, which is what
    `NotificationService.delayed_send` calls inside the worker. `delayed_send` here only
    records the ids it was handed, so a test can tell the two entrypoints apart.
    """

    notification_type = NotificationTypes.EMAIL

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.delayed_send_notification_ids: list[int | str | uuid.UUID] = []

    def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        self.delayed_send_notification_ids.append(notification_id)


class InvalidAdapter:
    def __init__(self, *_args, **_kwargs):
        pass
