from abc import abstractmethod
from typing import TYPE_CHECKING

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
    NotificationSendInput,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


class TemplatedSMS(NotificationSendInput):
    def __init__(self, body: str, template_version: int | None = None):
        self.body = body
        # See ``NotificationSendInput.template_version``. Keyword-defaulted so every existing
        # ``TemplatedSMS(body)`` call keeps working untouched.
        self.template_version = template_version


class BaseTemplatedSMSRenderer(BaseNotificationTemplateRenderer):
    @abstractmethod
    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> TemplatedSMS: ...

    @abstractmethod
    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: str,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedSMS: ...
