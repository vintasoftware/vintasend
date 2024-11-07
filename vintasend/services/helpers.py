from collections.abc import Iterable
from typing import Any, cast

from vintasend.app_settings import NotificationSettings
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
)


def _import_class(import_string: str) -> Any:
    module_name, class_name = import_string.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def get_notification_adapters(
    adapters_imports_strs: Iterable[tuple[str, str]] | None,
    backend: str | None = None,
    backend_kwargs: dict | None = None,
) -> list[BaseNotificationAdapter]:
    default_adapters = []
    adapters_imports_strs_with_default = (
        adapters_imports_strs
        if adapters_imports_strs is not None
        else NotificationSettings().NOTIFICATION_ADAPTERS
    )
    for adapter_import_string, template_renderer_import_str in adapters_imports_strs_with_default:
        try:
            adapter_cls = _import_class(adapter_import_string)
        except (ImportError, ModuleNotFoundError) as e:
            raise ValueError(
                f"Notifications Adapter Error: Could not import {adapter_import_string}"
            ) from e

        try:
            adapter = adapter_cls(
                template_renderer_import_str,
                backend if backend else NotificationSettings().NOTIFICATION_BACKEND,
                backend_kwargs,
            )
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"Notifications Adapter Error: Could not instantiate {adapter_import_string}"
            ) from e

        if not isinstance(adapter, BaseNotificationAdapter):
            raise ValueError(
                f"Notifications Adapter Error: {adapter_import_string} is not a valid notification adapter"
            )

        default_adapters.append(cast(BaseNotificationAdapter, adapter))
    return default_adapters


def get_notification_backend(
    backend_import_str: str | None, backend_kwargs: dict | None = None
) -> BaseNotificationBackend:
    backend_import_str_with_fallback = (
        backend_import_str
        if backend_import_str is not None
        else NotificationSettings().NOTIFICATION_BACKEND
    )

    try:
        backend_cls = _import_class(backend_import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Backend Error: Could not import {backend_import_str_with_fallback}"
        ) from e

    try:
        backend = backend_cls(**backend_kwargs) if backend_kwargs else backend_cls()
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Backend Error: Could not instantiate {backend_import_str_with_fallback}"
        ) from e

    if not isinstance(backend, BaseNotificationBackend):
        raise ValueError(
            f"Notifications Backend Error: {backend_import_str_with_fallback} is not a valid notification backend"
        )
    return cast(BaseNotificationBackend, backend)


def get_template_renderer(template_renderer_import_str: str) -> BaseTemplatedEmailRenderer:
    try:
        template_renderer_cls = _import_class(template_renderer_import_str)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Template Renderer Error: Could not import {template_renderer_import_str}"
        ) from e

    try:
        template_renderer = template_renderer_cls()
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Template Renderer Error: Could not instantiate {template_renderer_import_str}"
        ) from e
    
    if not isinstance(template_renderer, BaseTemplatedEmailRenderer):
        raise ValueError(
            f"Notifications Template Renderer Error: {template_renderer_import_str} is not a valid template renderer"
        )

    return cast(BaseTemplatedEmailRenderer, template_renderer)
