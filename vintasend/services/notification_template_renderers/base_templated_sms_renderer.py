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
    def __init__(self, body: str):
        self.body = body


class BaseTemplatedSMSRenderer(BaseNotificationTemplateRenderer):
    @abstractmethod
    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> TemplatedSMS: ...
