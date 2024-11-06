from typing import TYPE_CHECKING

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.base import BaseNotificationAdapter


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


class FakeInAppAdapter(BaseNotificationAdapter):
    notification_type = NotificationTypes.IN_APP

    def __init__(
        self, backend: str | None, template_renderer: str | None, backend_kwargs: dict | None
    ) -> None:
        self.backend = backend
        self.backend_kwargs = backend_kwargs
        self.template_renderer = template_renderer
        self.sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.sent_emails.append((notification, context))
