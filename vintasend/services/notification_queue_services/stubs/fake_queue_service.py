import uuid

from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService


class FakeQueueService(BaseNotificationQueueService):
    """In-memory queue service used by tests and as a reference implementation.

    Records every enqueued notification id in ``self.enqueued_notification_ids`` instead of
    dispatching to a real task queue.
    """

    def __init__(self) -> None:
        self.enqueued_notification_ids: list[int | str | uuid.UUID] = []

    def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None:
        self.enqueued_notification_ids.append(notification_id)


class FakeAsyncIOQueueService(AsyncIOBaseNotificationQueueService):
    """In-memory AsyncIO queue service used by tests and as a reference implementation.

    Records every enqueued notification id in ``self.enqueued_notification_ids`` instead of
    dispatching to a real task queue.
    """

    def __init__(self) -> None:
        self.enqueued_notification_ids: list[int | str | uuid.UUID] = []

    async def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None:
        self.enqueued_notification_ids.append(notification_id)
