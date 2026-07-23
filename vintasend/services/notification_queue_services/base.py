import uuid
from abc import ABC, abstractmethod


class BaseNotificationQueueService(ABC):
    """
    Base class for notification queue services. All notification queue services should
    inherit from this class.

    A queue service enqueues a notification id so a background worker can pick it up later.
    It does not carry the notification's data across the wire. The worker reloads the
    notification from the backend by id.

    Implementers must catch broker and transport failures and re-raise them as a
    `NotificationError` subclass. `NotificationService.send()` calls `enqueue_notification`
    inside its adapter loop and does not expect a raw broker exception (for example
    `kombu.exceptions.OperationalError`) to escape. An unwrapped exception would propagate
    through `send()` regardless of `raise_on_failed_send`, breaking that setting's contract.

    Returning normally from `enqueue_notification` means the broker has accepted the
    notification id. It does not mean the worker has received or processed it yet.

    `send()` calls this method synchronously as part of its adapter loop, so an
    implementation should return quickly and not block on the worker finishing its work.

    Delivery is at-least-once. The worker may receive the same notification id more than
    once, so the code that consumes it must tolerate redelivery and handle it safely.
    """

    @abstractmethod
    def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None: ...
