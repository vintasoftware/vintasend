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
        Deliver the notification identified by `notification_id` from a background worker.

        Only the id crosses the queue: nothing about the notification is serialized into the
        task payload, so an implementation reads whatever it needs -- including attachment
        file handles -- from the backend.

        Delivery is at-least-once, so an implementation may be handed the same id twice and
        must tolerate that.

        :param notification_id: The id of the notification to deliver.
        """
        ...
