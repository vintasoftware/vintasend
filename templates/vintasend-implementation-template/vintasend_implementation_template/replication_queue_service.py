"""Replication queue seam stub.

Subclass ``BaseNotificationReplicationQueueService`` (and its AsyncIO twin) to hand a
``(notification_id, backend_identifier)`` pair off to a background worker -- Celery, RQ, an SQS
queue, whatever this integration targets. The replication queue carries only the notification id
and the destination backend's identifier across the wire; the worker reloads the snapshot from the
primary backend by id and calls back into the service's ``process_replication`` to converge that
replica.
"""

import uuid

from vintasend.services.notification_queue_services.asyncio_replication_base import (
    AsyncIOBaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.replication_base import (
    BaseNotificationReplicationQueueService,
)


class ImplementationTemplateReplicationQueueService(BaseNotificationReplicationQueueService):
    """TODO: rename and implement. See ``vintasend/services/notification_queue_services/replication_base.py``."""

    def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None:
        """TODO: implement enqueue_replication — see vintasend/services/notification_queue_services/replication_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement enqueue_replication — see "
            "vintasend/services/notification_queue_services/replication_base.py for the contract"
        )


class ImplementationTemplateAsyncIOReplicationQueueService(
    AsyncIOBaseNotificationReplicationQueueService
):
    """TODO: rename and implement. See ``vintasend/services/notification_queue_services/asyncio_replication_base.py``."""

    async def enqueue_replication(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str
    ) -> None:
        """TODO: implement enqueue_replication — see vintasend/services/notification_queue_services/asyncio_replication_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement enqueue_replication — see "
            "vintasend/services/notification_queue_services/asyncio_replication_base.py for the contract"
        )
