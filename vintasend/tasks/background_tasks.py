import logging

from vintasend.services.helpers import get_notification_adapters
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter, NotificationDict
from vintasend.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


def send_notification(
    notification: NotificationDict,
    context: dict,
    adapters: list[tuple[str, str]] | None = None,
    backend: str | None = None,
    backend_kwargs: dict | None = None,
    config: dict | None = None,
):
    adapter_intances = get_notification_adapters(adapters, backend, backend_kwargs)
    if isinstance(adapter_intances[0], AsyncBaseNotificationAdapter):
        desserialized_backend_kwargs = (
            adapter_intances[0].restore_backend_kwargs(backend_kwargs) if backend_kwargs else None
        )
        desserialized_config = (
            adapter_intances[0].restore_config(config) if config else None
        )

    try:
        NotificationService(
            adapters, backend, desserialized_backend_kwargs, desserialized_config
        ).delayed_send(notification, context)
    except Exception as e:
        logger.exception("Error sending notification: %s", e)
