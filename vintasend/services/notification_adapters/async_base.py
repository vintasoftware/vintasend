import uuid
from abc import abstractmethod
from typing import Generic, TypeVar

from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class AsyncBaseNotificationAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    """
    Marker base class for adapters whose delivery happens in a background worker.

    Despite the name this is not an `async`/`await` adapter -- see
    `asyncio_base.AsyncIOBaseNotificationAdapter` for that one. Subclassing this class is
    how an adapter declares "do not deliver me in the web process; hand my notification id
    to the queue service and let a worker deliver it".

    `NotificationService.send()` never calls `send()` on one of these adapters. It enqueues
    the notification id through its configured queue service instead. The worker then calls
    `NotificationService.delayed_send(notification_id)`, which reloads the notification,
    generates the context at delivery time and calls `send()` -- so `send()` is where a
    background adapter's real delivery work belongs.
    """

    @abstractmethod
    def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Mark this adapter for background delivery. Core never calls this method.

        Having this abstract method is what makes an adapter subclass
        `AsyncBaseNotificationAdapter` instead of `BaseNotificationAdapter`, and that is
        the only role it plays. The real delivery work belongs in `send()`, inherited from
        `BaseNotificationAdapter`. See the class docstring above for how the worker calls
        `NotificationService.delayed_send(notification_id)`, which reloads the notification,
        generates the context, and calls `send()`.

        Because of that path, `send()` on a background adapter only receives the notification
        id from the queue; it reads everything else, including attachment file handles, from
        the backend. Delivery is at-least-once, so `send()` may be handed the same notification
        twice and must tolerate that.

        :param notification_id: The id of the notification. Present to satisfy the marker
            role described above; core never passes a value here.
        """
        ...
