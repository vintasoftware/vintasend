import datetime
from typing import TYPE_CHECKING, cast, Generic, TypeVar, overload

from vintasend.constants import NotificationTypes
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
from vintasend.services.helpers import get_notification_backend, get_template_renderer
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import BaseTemplateRenderer


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseTemplateRenderer)

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


class FakeAsyncEmailAdapter(AsyncBaseNotificationAdapter, Generic[B, T], FakeEmailAdapter[B, T]):
    notification_type = NotificationTypes.EMAIL

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