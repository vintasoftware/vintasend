from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar, overload

from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import BaseTemplateRenderer


if TYPE_CHECKING:
    from vintasend.constants import NotificationTypes
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseTemplateRenderer)

class BaseNotificationAdapter(Generic[B, T], ABC):
    """
    Base class for notification adapters. All notification adapters should inherit from this class.

    The notification adapter is responsible for sending notifications to the user and also for
    marking them as sent or failed.
    """

    notification_type: "NotificationTypes"

    backend: B
    template_renderer: T

    @overload
    def __init__(self, template_renderer: T, backend: B, backend_kwargs: None = None) -> None:
        ...
    
    @overload
    def __init__(self, template_renderer: str, backend: str, backend_kwargs: dict | None = None) -> None:
        ...

    @overload
    def __init__(self, template_renderer: str, backend: B, backend_kwargs: None = None) -> None:
        ...

    @overload
    def __init__(self, template_renderer: T, backend: str, backend_kwargs: dict | None = None) -> None:
        ...

    @abstractmethod
    def __init__(self, template_renderer: T | str, backend: B | str | None, backend_kwargs: dict | None = None) -> None:
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
