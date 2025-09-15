from typing import TYPE_CHECKING

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
    NotificationSendInput,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification, OneOffNotification
    from vintasend.services.notification_service import NotificationContextDict


class TemplatedSMS(NotificationSendInput):
    def __init__(self, body: str):
        self.body = body


class BaseTemplatedSMSRenderer(BaseNotificationTemplateRenderer):
    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> TemplatedSMS:
        raise NotImplementedError(
            "This method should be implemented in subclasses to render the SMS body."
        )
