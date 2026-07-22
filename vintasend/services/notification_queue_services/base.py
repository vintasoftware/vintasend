import uuid
from abc import ABC, abstractmethod


class BaseNotificationQueueService(ABC):
    """
    Base class for notification queue services. All notification queue services should
    inherit from this class.

    A queue service is responsible for enqueueing a notification id so a background worker
    can pick it up later. It does not carry the notification's data across the wire — the
    worker reloads the notification from the backend by id.
    """

    @abstractmethod
    def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None: ...
