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

    def __init__(
        self, template_renderer: T | str, backend: B | str | None, backend_kwargs: dict | None = None
    ) -> None:
        if backend is not None and isinstance(backend, BaseNotificationBackend):
            self.backend = cast(B, backend)
        else:
            self.backend = cast(B, get_notification_backend(backend, backend_kwargs))
        if isinstance(template_renderer, str):
            self.template_renderer = cast(T, get_template_renderer(template_renderer))
        else:
            self.template_renderer = template_renderer
        self.sent_emails = []

    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        self.template_renderer.render(notification, context)
        self.sent_emails.append((notification, context))


class FakeAsyncEmailAdapter(Generic[B, T], FakeEmailAdapter[B, T], AsyncBaseNotificationAdapter):
    notification_type = NotificationTypes.EMAIL

    def __init__(
        self, template_renderer: T | str, backend: B | str | None, backend_kwargs: dict | None = None
    ) -> None:
        if backend is not None and isinstance(backend, BaseNotificationBackend):
            self.backend = cast(B, backend)
        else:
            self.backend = cast(B, get_notification_backend(backend, backend_kwargs))
        if isinstance(template_renderer, str):
            self.template_renderer = cast(T, get_template_renderer(template_renderer))
        else:
            self.template_renderer = template_renderer
        self.sent_emails = []

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