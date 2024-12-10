from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, TypeVar, cast, overload

from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


if TYPE_CHECKING:
    from vintasend.constants import NotificationTypes
    from vintasend.services.dataclasses import Notification
    from vintasend.services.notification_service import NotificationContextDict


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class BaseNotificationAdapter(Generic[B, T], ABC):
    """
    Base class for notification adapters. All notification adapters should inherit from this class.

    The notification adapter is responsible for sending notifications to the user and also for
    marking them as sent or failed.
    """

    notification_type: "NotificationTypes"

    backend: B
    template_renderer: T
    adapter_import_str: str
    adapter_kwargs: dict

    @overload
    def __init__(
        self,
        template_renderer: T,
        backend: B,
        backend_kwargs: None = None,
        config: Any = None,
        **kwargs,
    ) -> None: ...

    @overload
    def __init__(
        self,
        template_renderer: str | tuple[str, dict[str, Any]],
        backend: str,
        backend_kwargs: dict | None = None,
        config: Any = None,
        **kwargs,
    ) -> None: ...

    @overload
    def __init__(
        self,
        template_renderer: str | tuple[str, dict[str, Any]],
        backend: B,
        backend_kwargs: None = None,
        config: Any = None,
        **kwargs,
    ) -> None: ...

    @overload
    def __init__(
        self,
        template_renderer: T,
        backend: str,
        backend_kwargs: dict | None = None,
        config: Any = None,
        **kwargs,
    ) -> None: ...

    def __init__(
        self,
        template_renderer: T | str | tuple[str, dict[str, Any]],
        backend: B | str | None,
        backend_kwargs: dict | None = None,
        config: Any = None,
        **kwargs,
    ) -> None:
        """
        Initialize the notification adapter.

        :param template_renderer: The template renderer to use to render the notification templates.
        :param backend: The backend to use to persist the notifications.
        :param backend_kwargs: The backend kwargs to pass to the backend in case backend is an import string.
        """
        from vintasend.services.helpers import get_notification_backend, get_template_renderer

        self.adapter_kwargs = kwargs

        if backend is not None and isinstance(backend, BaseNotificationBackend):
            self.backend = cast(B, backend)
        else:
            self.backend = cast(B, get_notification_backend(backend, backend_kwargs, config))
        
        if isinstance(template_renderer, str) or isinstance(template_renderer, tuple):
            self.template_renderer = cast(T, get_template_renderer(template_renderer))
        else:
            self.template_renderer = template_renderer

        self.adapter_import_str = f"{self.__module__}.{self.__class__.__name__}"
        self.config = config

    @abstractmethod
    def send(self, notification: "Notification", context: "NotificationContextDict") -> None:
        """
        Send the notification to the user.

        :param notification: The notification to send.
        :param context: The context to render the notification templates.
        """
        raise NotImplementedError
