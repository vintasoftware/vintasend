from typing import TYPE_CHECKING, ClassVar

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


class FakeVersionedTemplateRenderer(BaseTemplatedEmailRenderer):
    """A renderer whose templates are versioned, standing in for a store-backed one.

    ``latest_version`` is a class attribute so a test can move the "current" version between
    calls and watch what a pinned notification does about it -- which is the whole point of
    pinning, and impossible to observe against a renderer that reads files.

    ``render`` reports whichever version it was asked for, falling back to the latest, which
    is exactly the rule ``ManagedTemplateRenderer`` follows against a real store.
    """

    latest_version: ClassVar[int] = 7

    def get_latest_template_version(self, template_key: str) -> int | None:
        return self.latest_version

    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        return TemplatedEmail(
            subject=notification.subject_template,
            body=notification.body_template,
            template_version=notification.requested_template_version or self.latest_version,
        )

    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: EmailTemplateContent,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        return TemplatedEmail(
            subject=template_content.subject_template,
            body=template_content.body_template,
            preheader=template_content.preheader_template,
            template_version=notification.requested_template_version or self.latest_version,
        )


class FakeRaisingVersionTemplateRenderer(FakeTemplateRenderer):
    """Versions templates in principle and fails to say which -- a store that is down.

    Pinning is best-effort, so the service is expected to log this and create the
    notification unpinned rather than fail the write.
    """

    def get_latest_template_version(self, template_key: str) -> int | None:
        raise RuntimeError("template store is unreachable")
