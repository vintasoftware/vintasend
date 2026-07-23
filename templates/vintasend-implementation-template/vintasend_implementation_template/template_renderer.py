"""Rendering seam stub.

Subclass ``BaseNotificationTemplateRenderer`` (or, more likely, one of its typed subclasses
``BaseTemplatedEmailRenderer`` / ``BaseTemplatedSMSRenderer``) to turn a notification's template
plus context into the input its adapter needs to send. There is no AsyncIO twin for this seam --
``render`` stays synchronous everywhere; async adapters call it directly.
"""

from typing import TYPE_CHECKING

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
    NotificationSendInput,
)
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
    EmailTemplateContent,
    TemplatedEmail,
)
from vintasend.services.notification_template_renderers.base_templated_sms_renderer import (
    BaseTemplatedSMSRenderer,
    TemplatedSMS,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


class ImplementationTemplateTemplateRenderer(BaseNotificationTemplateRenderer):
    """TODO: rename and implement. See ``vintasend/services/notification_template_renderers/base.py``."""

    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> NotificationSendInput:
        """TODO: implement render — see vintasend/services/notification_template_renderers/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement render — see "
            "vintasend/services/notification_template_renderers/base.py for the contract"
        )


class ImplementationTemplateEmailRenderer(BaseTemplatedEmailRenderer):
    """TODO: rename and implement. See ``vintasend/services/notification_template_renderers/base_templated_email_renderer.py``."""

    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        """TODO: implement render — see vintasend/services/notification_template_renderers/base_templated_email_renderer.py for the contract."""
        raise NotImplementedError(
            "TODO: implement render — see "
            "vintasend/services/notification_template_renderers/base_templated_email_renderer.py "
            "for the contract"
        )

    def render_from_template_content(
        self,
        notification: "Notification | OneOffNotification",
        template_content: EmailTemplateContent,
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        """TODO: implement render_from_template_content — see vintasend/services/notification_template_renderers/base_templated_email_renderer.py for the contract."""
        raise NotImplementedError(
            "TODO: implement render_from_template_content — see "
            "vintasend/services/notification_template_renderers/base_templated_email_renderer.py "
            "for the contract"
        )


class ImplementationTemplateSMSRenderer(BaseTemplatedSMSRenderer):
    """TODO: rename and implement. See ``vintasend/services/notification_template_renderers/base_templated_sms_renderer.py``."""

    def render(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> TemplatedSMS:
        """TODO: implement render — see vintasend/services/notification_template_renderers/base_templated_sms_renderer.py for the contract."""
        raise NotImplementedError(
            "TODO: implement render — see "
            "vintasend/services/notification_template_renderers/base_templated_sms_renderer.py "
            "for the contract"
        )
