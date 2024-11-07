import datetime
from typing import TYPE_CHECKING

from vintasend.constants import NotificationTypes
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
from vintasend.services.helpers import get_template_renderer


class FakeEmailAdapter(BaseNotificationAdapter):
    notification_type = NotificationTypes.EMAIL

    def __init__(
        self, template_renderer: str | None, backend: str | None, backend_kwargs: dict | None
    ) -> None:
        self.backend = backend
        self.backend_kwargs = backend_kwargs
        self.template_renderer = get_template_renderer(template_renderer)
        self.sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))


class FakeAsyncEmailAdapter(FakeEmailAdapter, AsyncBaseNotificationAdapter):
    notification_type = NotificationTypes.EMAIL

    def __init__(
        self, template_renderer: str | None, backend: str | None, backend_kwargs: dict | None
    ) -> None:
        self.backend = backend
        self.backend_kwargs = backend_kwargs
        self.template_renderer = get_template_renderer(template_renderer)
        self.sent_emails: list[tuple["Notification", "NotificationContextDict"]] = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        pass

    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
        notification = self.notification_from_dict(notification_dict)
        context = NotificationContextDict(**context_dict)
        super().send(notification, context)

    def notification_from_dict(self, notification_dict: dict) -> "Notification":
        notification_dict["send_after"] = (
            datetime.datetime.fromisoformat(notification_dict["send_after"])
            if notification_dict["send_after"]
            else None
        )
        return Notification(**notification_dict)

class InvalidAdapter():
    def __init__(self, *_args, **_kwargs):
        pass