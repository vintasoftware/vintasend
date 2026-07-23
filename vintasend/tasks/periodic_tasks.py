"""Periodic entrypoint for draining notifications whose send time has arrived."""

from typing import Any

from vintasend.services.notification_service import NotificationService
from vintasend.tasks.background_tasks import get_notification_service


def periodic_send_pending_notifications(
    notification_service: "NotificationService[Any, Any] | None" = None,
) -> None:
    """
    Send every pending notification, on a schedule.

    Like `send_notification`, this runs outside the web process and gets its service from
    `NOTIFICATION_SERVICE_FACTORY` -- cached per process and shared with the background-send
    entrypoint.

    :param notification_service: an explicit service, for hosts that would rather wire it
        themselves than go through NOTIFICATION_SERVICE_FACTORY.
    """
    service = (
        notification_service if notification_service is not None else get_notification_service()
    )
    service.send_pending_notifications()
