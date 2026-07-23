"""Background-send queue seam stub.

Subclass ``BaseNotificationQueueService`` (and its AsyncIO twin) to hand a notification id off to
a background worker -- Celery, RQ, an SQS queue, whatever this integration targets. The queue
service carries only the notification id across the wire; the worker reloads the notification
from the backend by id and calls back into the adapter's ``delayed_send``.
"""

import uuid

from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService


class ImplementationTemplateQueueService(BaseNotificationQueueService):
    """TODO: rename and implement. See ``vintasend/services/notification_queue_services/base.py``."""

    def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """TODO: implement enqueue_notification — see vintasend/services/notification_queue_services/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement enqueue_notification — see "
            "vintasend/services/notification_queue_services/base.py for the contract"
        )


class ImplementationTemplateAsyncIOQueueService(AsyncIOBaseNotificationQueueService):
    """TODO: rename and implement. See ``vintasend/services/notification_queue_services/asyncio_base.py``."""

    async def enqueue_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """TODO: implement enqueue_notification — see vintasend/services/notification_queue_services/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement enqueue_notification — see "
            "vintasend/services/notification_queue_services/asyncio_base.py for the contract"
        )
