"""Delivery seam stub.

Subclass ``BaseNotificationAdapter`` (and its AsyncIO twin) to actually deliver a rendered
notification -- send an email, post to a push service, whatever this integration targets. Use
``BackgroundNotificationAdapter`` / ``AsyncIOBackgroundNotificationAdapter`` instead of the plain
bases when delivery should happen in a worker rather than the calling process; see each base's
docstring in ``vintasend`` for the enqueue/``delayed_send`` split.
"""

import uuid
from typing import TYPE_CHECKING, Generic, TypeVar

from vintasend.constants import NotificationTypes
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
from vintasend.services.notification_adapters.asyncio_background_base import (
    AsyncIOBackgroundNotificationAdapter,
)
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


B = TypeVar("B", bound=BaseNotificationBackend)
BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class ImplementationTemplateAdapter(Generic[B, T], BaseNotificationAdapter[B, T]):
    """TODO: rename and implement. See ``vintasend/services/notification_adapters/base.py``."""

    # TODO: pick the vintasend.constants.NotificationTypes member this adapter actually sends.
    notification_type = NotificationTypes.EMAIL

    def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        """TODO: implement send — see vintasend/services/notification_adapters/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement send — see "
            "vintasend/services/notification_adapters/base.py for the contract"
        )


class ImplementationTemplateAsyncIOAdapter(
    Generic[BAIO, T], AsyncIOBaseNotificationAdapter[BAIO, T]
):
    """TODO: rename and implement. See ``vintasend/services/notification_adapters/asyncio_base.py``."""

    # TODO: pick the vintasend.constants.NotificationTypes member this adapter actually sends.
    notification_type = NotificationTypes.EMAIL

    async def send(
        self, notification: "Notification | OneOffNotification", context: "NotificationContextDict"
    ) -> None:
        """TODO: implement send — see vintasend/services/notification_adapters/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement send — see "
            "vintasend/services/notification_adapters/asyncio_base.py for the contract"
        )


class ImplementationTemplateBackgroundAdapter(
    BackgroundNotificationAdapter, Generic[B, T], ImplementationTemplateAdapter[B, T]
):
    """TODO: rename and implement. See ``vintasend/services/notification_adapters/async_base.py``.

    Delivery still belongs in ``send`` (inherited from ``ImplementationTemplateAdapter`` above);
    only ``delayed_send`` is specific to the background marker.
    """

    def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """TODO: implement delayed_send — see vintasend/services/notification_adapters/async_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delayed_send — see "
            "vintasend/services/notification_adapters/async_base.py for the contract"
        )


class ImplementationTemplateAsyncIOBackgroundAdapter(
    AsyncIOBackgroundNotificationAdapter,
    Generic[BAIO, T],
    ImplementationTemplateAsyncIOAdapter[BAIO, T],
):
    """TODO: rename and implement. See ``vintasend/services/notification_adapters/asyncio_background_base.py``.

    Delivery still belongs in ``send`` (inherited from ``ImplementationTemplateAsyncIOAdapter``
    above); only ``delayed_send`` is specific to the background marker.
    """

    async def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """TODO: implement delayed_send — see vintasend/services/notification_adapters/asyncio_background_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delayed_send — see "
            "vintasend/services/notification_adapters/asyncio_background_base.py for the contract"
        )
