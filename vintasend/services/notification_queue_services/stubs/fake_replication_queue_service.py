import uuid

from vintasend.services.notification_queue_services.asyncio_replication_base import (
    AsyncIOBaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.replication_base import (
    BaseNotificationReplicationQueueService,
)


class FakeReplicationQueueService(BaseNotificationReplicationQueueService):
    """In-memory replication queue service used by tests and as a reference implementation.

    Records every enqueued ``(notification_id, backend_identifier)`` pair in
    ``self.enqueued_replications`` instead of dispatching to a real task queue.
    """

    def __init__(self) -> None:
        self.enqueued_replications: list[tuple[int | str | uuid.UUID, str]] = []

    def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None:
        self.enqueued_replications.append((notification_id, backend_identifier))


class FakeAsyncIOReplicationQueueService(AsyncIOBaseNotificationReplicationQueueService):
    """In-memory AsyncIO replication queue service used by tests and as a reference
    implementation.

    Records every enqueued ``(notification_id, backend_identifier)`` pair in
    ``self.enqueued_replications`` instead of dispatching to a real task queue.
    """

    def __init__(self) -> None:
        self.enqueued_replications: list[tuple[int | str | uuid.UUID, str]] = []

    async def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None:
        self.enqueued_replications.append((notification_id, backend_identifier))
