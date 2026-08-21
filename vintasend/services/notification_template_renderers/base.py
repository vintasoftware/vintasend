from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, TypeVar

from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


class NotificationSendInput:
    """
    Represents the input necessary for the send method.
    """

    # Which version of the template this render actually used, reported back so the service
    # can record it on the notification. ``None`` from every renderer that does not version
    # templates, which is all of them except the store-backed ones -- a file on disk has no
    # version to report.
    #
    # A plain class attribute rather than a dataclass field, so it is set the same way on
    # every send input: the two shipped here declare it as a field as well (both already end
    # in defaulted fields, so it costs their constructors nothing), while a renderer holding
    # any other ``NotificationSendInput`` subclass assigns it after the fact.
    template_version: int | None = None


@dataclass
class TemplateContent:
    body_template: str


TemplateContentType = TypeVar("TemplateContentType", bound=TemplateContent)


class BaseNotificationTemplateRenderer(Generic[TemplateContentType], ABC):
    """
    Base class for notification template renderers. All notification template renderers should inherit from this class.

    The notification template renderer is responsible for rendering the notification templates.
    """

    template_renderer_import_str: str
    template_renderer_kwargs: dict

    def __init__(self, **kwargs):
        self.template_renderer_import_str = get_class_path(self)
        self.template_renderer_kwargs = kwargs

    def get_latest_template_version(self, template_key: str) -> int | None:
        """The newest version of a template, for a renderer whose templates are versioned.

        Concrete and returning ``None`` on purpose: templates in a file tree have no version
        to report, and that is most renderers. A renderer reading from a store that versions
        them -- ``vintasend-managed-templates`` and anything like it -- overrides this.

        ``NotificationService`` calls it to pin a notification to the version that is current
        the moment it is created, so a later edit to the template cannot change what an
        already-recorded notification renders. ``None`` simply means there is nothing to pin,
        and the notification keeps resolving its template the way it always has.

        Best-effort by contract: raise if you like -- the service logs it and carries on
        without a pin rather than failing the write -- but returning ``None`` for a template
        that does not exist is kinder than raising, since a missing template is the send's
        problem to report, not the creation's.

        :param template_key: what the notification's ``body_template`` names.
        :return: the newest version number, or None if this renderer does not version
            templates (or cannot find that one).
        """
        return None

    @abstractmethod
    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> NotificationSendInput:
        """
        Render the notification template.

        :param notification: The notification to render.
        :return: The input necessary to send the notification.
        """

    @abstractmethod
    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: TemplateContentType,
        context: "NotificationContextDict",
        **kwargs,
    ) -> NotificationSendInput:
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
