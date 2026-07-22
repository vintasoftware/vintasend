import uuid
from abc import abstractmethod
from typing import Generic, TypeVar

from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


B = TypeVar("B", bound=AsyncIOBaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class AsyncIOBackgroundNotificationAdapter(Generic[B, T], AsyncIOBaseNotificationAdapter[B, T]):
    """
    Marker base class for AsyncIO adapters whose delivery happens in a background worker.

    This is the genuine `async`/`await` counterpart of
    `async_base.BackgroundNotificationAdapter` -- see that module's docstring for why that
    one's name, despite appearances, has nothing to do with async/await. This class composes
    with `asyncio_base.AsyncIOBaseNotificationAdapter`, the real async/await adapter base.
    Subclassing this class is how an AsyncIO adapter declares "do not deliver me in the web
    process; hand my notification id to the queue service and let a worker deliver it".

    `AsyncIONotificationService.send()` never calls `send()` on one of these adapters. It
    awaits its configured queue service's `enqueue_notification` instead. The worker then
    calls `AsyncIONotificationService.delayed_send(notification_id)`, which reloads the
    notification, generates the context at delivery time and awaits `send()` -- so `send()`
    is where a background adapter's real delivery work belongs.
    """

    @abstractmethod
    async def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Mark this adapter for background delivery. Core never calls this method.

        Having this abstract method is what makes an adapter subclass
        `AsyncIOBackgroundNotificationAdapter` instead of `AsyncIOBaseNotificationAdapter`,
        and that is the only role it plays. The real delivery work belongs in `send()`,
        inherited from `AsyncIOBaseNotificationAdapter`. See the class docstring above for
        how the worker calls `AsyncIONotificationService.delayed_send(notification_id)`,
        which reloads the notification, generates the context, and awaits `send()`.

        Because of that path, `send()` on a background adapter only receives the notification
        id from the queue; it reads everything else, including attachment file handles, from
        the backend. Delivery is at-least-once, so `send()` may be handed the same notification
        twice and must tolerate that.

        :param notification_id: The id of the notification. Present to satisfy the marker
            role described above; core never passes a value here.
        """
        ...
