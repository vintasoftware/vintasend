from typing import TYPE_CHECKING

from vintasend.exceptions import NotificationBodyTemplateRenderingError
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
    EmailTemplateContent,
    TemplatedEmail,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


class FakeTemplateRenderer(BaseTemplatedEmailRenderer):
    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        return TemplatedEmail(
            subject=notification.subject_template, body=notification.body_template
        )

    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: EmailTemplateContent,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        # Renders the SUPPLIED content, never the notification's stored subject_template /
        # body_template -- that is the whole point of this method.
        return TemplatedEmail(
            subject=template_content.subject_template,
            body=template_content.body_template,
            preheader=template_content.preheader_template,
        )


class FakeTemplateRendererWithException(BaseTemplatedEmailRenderer):
    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        raise NotificationBodyTemplateRenderingError("Fake error")

    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: EmailTemplateContent,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        raise NotificationBodyTemplateRenderingError("Fake error")


class InvalidTemplateRenderer:
    pass


class FakeTemplateRendererWithExceptionOnInit(FakeTemplateRendererWithException):
    def __init__(self):
        raise NotificationBodyTemplateRenderingError("Fake error")
