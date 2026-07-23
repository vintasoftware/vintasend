import uuid
from abc import ABC, abstractmethod


class AsyncIOBaseNotificationReplicationQueueService(ABC):
    """
    Base class for AsyncIO notification replication queue services. All AsyncIO replication
    queue services should inherit from this class.

    A replication queue service enqueues one task per destination backend so a background
    worker can replicate a notification off the request path. It carries only the notification
    id and the target backend's identifier across the wire; the worker reloads the snapshot from
    the primary backend by id and converges the named replica to it via ``process_replication``.

    This is the sibling of ``AsyncIOBaseNotificationQueueService``: the send queue hands a
    worker a notification id to deliver, while the replication queue hands a worker a
    ``(notification_id, backend_identifier)`` pair to reconcile. It reuses the same
    host-factory worker model rather than introducing a second queue mechanism.

    Implementers must catch broker and transport failures and re-raise them as a
    `NotificationError` subclass. `AsyncIONotificationService._execute_multi_backend_write`
    awaits `enqueue_replication` per additional backend in queued mode and does not expect a raw
    broker exception to escape: a failed enqueue is caught and that one backend is replicated
    inline instead, so a broken queue never silently drops replication.

    Returning normally from `enqueue_replication` means the broker has accepted the replication
    task. It does not mean the worker has replicated the record yet.

    Delivery is at-least-once. The worker may receive the same pair more than once, so
    `process_replication` is idempotent -- it converges a replica to the primary's snapshot,
    which is safe to repeat.
    """

    @abstractmethod
    async def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None: ...
