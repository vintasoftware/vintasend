from typing import TYPE_CHECKING, Generic, TypeVar, cast

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.helpers import get_notification_backend, get_template_renderer
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import BaseTemplateRenderer


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseTemplateRenderer)


class FakeInAppAdapter(BaseNotificationAdapter, Generic[B, T]):
    notification_type = NotificationTypes.IN_APP
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
