from abc import abstractmethod
from dataclasses import dataclass
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


@dataclass
class TemplatedEmail(NotificationSendInput):
    subject: str
    body: str
    # Optional: populated by renderers that render a preheader too. Defaults to None so
    # existing ``render`` implementations that never set it keep working unchanged.
    preheader: str | None = None


@dataclass
class EmailTemplateContent:
    """A historical subject/body (and optional preheader) template pair, supplied by the
    caller rather than looked up from a notification's stored template reference.

    Used by ``render_from_template_content`` to reproduce a past render -- for a preview or
    audit -- without touching the notification's currently configured templates.
    """

    subject_template: str
    body_template: str
    # Preheader is a Python-only concept with no TS counterpart, so it stays optional.
    preheader_template: str | None = None


class BaseTemplatedEmailRenderer(BaseNotificationTemplateRenderer):
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
    ) -> TemplatedEmail:
        """
        Render an email from supplied template content instead of the notification's stored
        template reference.

        Unlike ``render``, which looks up the notification's ``subject_template`` /
        ``body_template`` (and ``preheader_template``) to locate a template, this renders the
        given ``template_content`` directly -- typically an older template pair paired with a
        notification's stored ``context_used``, to reproduce how it rendered in the past.

        :param notification: The notification the render is performed on behalf of. Only its
            non-template fields (e.g. attachments) are consulted; its stored templates are
            ignored in favor of ``template_content``.
        :param template_content: The historical subject/body (and optional preheader) template
            content to render.
        :param context: The context to render with, verbatim -- typically a notification's
            stored ``context_used``. No context generation happens here.
        :return: The rendered email.
        """
