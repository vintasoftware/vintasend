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
    adapter_intances = get_notification_adapters(notification_adapters, backend_import_str, backend_kwargs)
    if isinstance(adapter_intances[0], AsyncBaseNotificationAdapter):
        desserialized_backend_kwargs = (
            adapter_intances[0].restore_backend_kwargs(backend_kwargs) if backend_kwargs else None
        )
        desserialized_config = adapter_intances[0].restore_config(config) if config else None
    
    NotificationService(
        notification_adapters=notification_adapters,
        notification_backend=backend_import_str,
        notification_backend_kwargs=desserialized_backend_kwargs,
        config=desserialized_config
    ).send_pending_notifications()
