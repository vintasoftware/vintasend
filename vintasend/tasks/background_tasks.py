import logging
from typing import Any

from vintasend.services.helpers import (
    get_notification_adapter_cls,
)
from vintasend.services.notification_adapters.async_base import (
    AsyncBaseNotificationAdapter,
    NotificationDict,
)
from vintasend.services.notification_service import NotificationService


logger = logging.getLogger(__name__)


def send_notification(
    notification: NotificationDict,
    context: dict,
    adapters: list[tuple[str | tuple[str, dict[str, Any]], str | tuple[str, dict[str, Any]]]],
    backend: str,
    backend_kwargs: dict | None = None,
    config: dict | None = None,
):
    # Get the adapter class, only a single adapter is supported
    adapter_cls = get_notification_adapter_cls(
        adapters[0][0] if isinstance(adapters[0][0], str) else adapters[0][0][0]
    )
    
    if not issubclass(adapter_cls, AsyncBaseNotificationAdapter):
        return
    
    desserialized_backend_kwargs = (
        adapter_cls.restore_backend_kwargs(backend_kwargs) if backend_kwargs else None
    )
    desserialized_config = (
        adapter_cls.restore_config(config) if config else None
    )
    desserialized_adapter_kwargs = (
        adapter_cls.restore_adapter_kwargs(adapters[0][0][1]) if isinstance(adapters[0][0], tuple) else {}
    )

    desserialized_template_renderer_kwargs = (
        adapter_cls.restore_template_renderer_kwargs(adapters[0][1][1])
        if isinstance(adapters[0][1], tuple)
        else {}
    )

    adapters_import_tuple = (
        (adapters[0][0][0], desserialized_adapter_kwargs), 
        (adapters[0][1][0], desserialized_template_renderer_kwargs)
    )
    try:
        service: NotificationService[Any, Any] = NotificationService(
            [adapters_import_tuple], backend, desserialized_backend_kwargs, desserialized_config
        )
        service.delayed_send(notification, context)
    except Exception as e:
        logger.exception("Error sending notification: %s", e)
