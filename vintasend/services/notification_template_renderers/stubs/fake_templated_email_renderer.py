from typing import TYPE_CHECKING

from vintasend.exceptions import NotificationBodyTemplateRenderingError
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
    TemplatedEmail,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification, OneOffNotification
    from vintasend.services.notification_service import NotificationContextDict


class FakeTemplateRenderer(BaseTemplatedEmailRenderer):
    def render(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict", **kwargs) -> TemplatedEmail:
        return TemplatedEmail(
            subject=notification.subject_template, body=notification.body_template
        )


class FakeTemplateRendererWithException(BaseTemplatedEmailRenderer):
    def render(self, notification: "Notification | OneOffNotification", context: "NotificationContextDict", **kwargs) -> TemplatedEmail:
        raise NotificationBodyTemplateRenderingError("Fake error")


class InvalidTemplateRenderer:
    pass


class FakeTemplateRendererWithExceptionOnInit(FakeTemplateRendererWithException):
    def __init__(self):
        raise NotificationBodyTemplateRenderingError("Fake error")
