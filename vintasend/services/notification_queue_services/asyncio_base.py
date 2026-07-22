import uuid
from abc import ABC, abstractmethod


class AsyncIOBaseNotificationQueueService(ABC):
    """
    Base class for AsyncIO notification queue services. All AsyncIO notification queue
    services should inherit from this class.

    A queue service is responsible for enqueueing a notification id so a background worker
    can pick it up later. It does not carry the notification's data across the wire — the
    worker reloads the notification from the backend by id.
    """

    @abstractmethod
    async def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None: ...
