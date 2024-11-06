from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from vintasend.constants import NotificationTypes
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


class BaseNotificationAdapter(ABC):
    """
    Base class for notification adapters. All notification adapters should inherit from this class.

    The notification adapter is responsible for sending notifications to the user and also for
    marking them as sent or failed.
    """

    notification_type: "NotificationTypes"

    @abstractmethod
    def __init__(self, backend: str | None, template_renderer: str) -> None:
        """
        Initialize the notification adapter.

        :param backend: The backend to use to persist the notifications.
        :param template_renderer: The template renderer to use to render the notification templates.
        """
        raise NotImplementedError

    @abstractmethod
    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        """
        Send the notification to the user.

        :param notification: The notification to send.
        :param context: The context to render the notification templates.
        """
        raise NotImplementedError
