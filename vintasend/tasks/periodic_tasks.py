from collections.abc import Iterable

from vintasend.services.helpers import get_notification_adapters
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
from vintasend.services.notification_service import NotificationService


def periodic_send_pending_notifications(
    notification_adapters: Iterable[tuple[str, str]] | None = None,
    backend_import_str: str | None = None,
    backend_kwargs: dict | None = None,
    config: dict | None = None,
):
    adapter_intances = get_notification_adapters(
        notification_adapters, backend_import_str, backend_kwargs
    )
    desserialized_backend_kwargs = None
    desserialized_config = None
    for adapter in adapter_intances:
        if isinstance(adapter, AsyncBaseNotificationAdapter):
            desserialized_backend_kwargs = (
                adapter.restore_backend_kwargs(backend_kwargs) if backend_kwargs else None
            )
            desserialized_config = adapter.restore_config(config) if config else None

            break

    if not desserialized_backend_kwargs or not desserialized_config:
        desserialized_backend_kwargs = backend_kwargs
        desserialized_config = config

    NotificationService(
        notification_adapters=notification_adapters,
        notification_backend=backend_import_str,
        notification_backend_kwargs=desserialized_backend_kwargs,
        config=desserialized_config,
    ).send_pending_notifications()
