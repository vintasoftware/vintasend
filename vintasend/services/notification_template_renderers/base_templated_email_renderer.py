from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
    NotificationSendInput,
    TemplateContent,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


@dataclass
class TemplatedEmail(NotificationSendInput):
    subject: str
    body: str
    # Optional: populated by renderers that render a preheader too. Defaults to None so
    # existing ``render`` implementations that never set it keep working unchanged.
    preheader: str | None = None
    # Which template version produced this, for a renderer whose templates are versioned.
    # See ``NotificationSendInput.template_version``; declared here as well so a renderer
    # that knows the version up front can pass it to the constructor.
    template_version: int | None = None


@dataclass
class EmailTemplateContent(TemplateContent):
    """A historical subject/body (and optional preheader) template pair, supplied by the
    caller rather than looked up from a notification's stored template reference.

    Used by ``render_from_template_content`` to reproduce a past render -- for a preview or
    audit -- without touching the notification's currently configured templates.
    """

    subject_template: str
    body_template: str
    # Preheader is a Python-only concept with no TS counterpart, so it stays optional.
    preheader_template: str | None = None


class BaseTemplatedEmailRenderer(BaseNotificationTemplateRenderer[EmailTemplateContent]):
    @abstractmethod
    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail: ...

    @abstractmethod
    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: EmailTemplateContent,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail: ...
