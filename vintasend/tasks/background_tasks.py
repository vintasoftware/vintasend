import logging

from vintasend.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


def send_notification(
    notification: dict,
    context: dict,
    adapters: list[tuple[str, str]] | None = None,
    backend: str | None = None,
    backend_kwargs: dict | None = None,
):
    NotificationService(adapters, backend, backend_kwargs).delayed_send(notification, context)
