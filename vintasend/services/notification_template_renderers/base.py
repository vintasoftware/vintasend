from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


class NotificationSendInput:
    """
    Represents the input necessary for the send method.
    """

    pass


class BaseNotificationTemplateRenderer(ABC):
    """
    Base class for notification template renderers. All notification template renderers should inherit from this class.

    The notification template renderer is responsible for rendering the notification templates.
    """

    @abstractmethod
    def render(
        self, notification: "Notification", context: "NotificationContextDict"
    ) -> NotificationSendInput:
        """
        Render the notification template.

        :param notification: The notification to render.
        :return: The input necessary to send the notification.
        """
        raise NotImplementedError
