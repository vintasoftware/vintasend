from typing import TYPE_CHECKING, Generic, TypeVar

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class FakeInAppAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    notification_type = NotificationTypes.IN_APP

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[
            tuple["Notification | OneOffNotification", "NotificationContextDict"]
        ] = []

    def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))


BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class FakeAsyncIOInAppAdapter(Generic[BAIO, T], AsyncIOBaseNotificationAdapter[BAIO, T]):
    notification_type = NotificationTypes.IN_APP

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails: list[
            tuple["Notification | OneOffNotification", "NotificationContextDict"]
        ] = []

    async def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))
