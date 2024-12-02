from typing import TYPE_CHECKING, Generic, TypeVar, cast

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class FakeInAppAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    notification_type = NotificationTypes.IN_APP
    sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.sent_emails = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))
