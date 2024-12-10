from vintasend.exceptions import NotificationBodyTemplateRenderingError
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
    TemplatedEmail,
)


class FakeTemplateRenderer(BaseTemplatedEmailRenderer):
    def render(self, notification, context):
        return TemplatedEmail(
            subject=notification.subject_template, body=notification.body_template
        )


class FakeTemplateRendererWithException(BaseTemplatedEmailRenderer):
    def render(self, notification, context):
        raise NotificationBodyTemplateRenderingError("Fake error")


class InvalidTemplateRenderer:
    pass


class FakeTemplateRendererWithExceptionOnInit(FakeTemplateRendererWithException):
    def __init__(self):
        raise NotificationBodyTemplateRenderingError("Fake error")
