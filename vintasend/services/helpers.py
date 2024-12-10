from collections.abc import Iterable
from typing import Any, cast

from vintasend.app_settings import NotificationSettings
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


def _import_class(import_string: str) -> Any:
    module_name, class_name = import_string.rsplit(".", 1)
    module = __import__(module_name, fromlist=[class_name])
    return getattr(module, class_name)


def get_asyncio_notification_adapter_cls(adapter_import_str: str) -> Any:
    try:
        adapter_cls = _import_class(adapter_import_str)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Adapter Error: Could not import {adapter_import_str}"
        ) from e

    if not issubclass(adapter_cls, AsyncIOBaseNotificationAdapter):
        raise ValueError(
            f"Notifications Adapter Error: {adapter_import_str} is not a valid AsyncIO notification adapter"
        )

    return adapter_cls


def get_notification_adapter_cls(adapter_import_str: str) -> Any:
    try:
        adapter_cls = _import_class(adapter_import_str)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Adapter Error: Could not import {adapter_import_str}"
        ) from e

    if not issubclass(adapter_cls, BaseNotificationAdapter):
        raise ValueError(
            f"Notifications Adapter Error: {adapter_import_str} is not a valid notification adapter"
        )

    return adapter_cls


def get_notification_adapters(
    adapters_imports_strs: Iterable[
        tuple[str | tuple[str, dict[str, Any]], str | tuple[str, dict[str, Any]]]
    ]
    | None,
    backend: str | None = None,
    backend_kwargs: dict | None = None,
    config: Any = None,
) -> list[BaseNotificationAdapter]:
    default_adapters = []
    app_settings = NotificationSettings(config)
    adapters_imports_strs_with_default = (
        adapters_imports_strs
        if adapters_imports_strs is not None
        else app_settings.NOTIFICATION_ADAPTERS
    )
    for adapter_import_str, template_renderer_import_str in adapters_imports_strs_with_default:
        adapter_kwargs: dict = {}
        if isinstance(adapter_import_str, tuple):
            adapter_import_str, adapter_kwargs = adapter_import_str

        try:
            adapter_cls = _import_class(adapter_import_str)
        except (ImportError, ModuleNotFoundError) as e:
            raise ValueError(
                f"Notifications Adapter Error: Could not import {adapter_import_str}"
            ) from e

        try:
            adapter = adapter_cls(
                template_renderer_import_str,
                backend if backend else app_settings.NOTIFICATION_BACKEND,
                backend_kwargs,
                config,
                **adapter_kwargs,
            )
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"Notifications Adapter Error: Could not instantiate {adapter_import_str}"
            ) from e

        if not isinstance(adapter, BaseNotificationAdapter):
            raise ValueError(
                f"Notifications Adapter Error: {adapter_import_str} is not a valid notification adapter"
            )

        default_adapters.append(cast(BaseNotificationAdapter, adapter))
    return default_adapters


def get_asyncio_notification_adapters(
    adapters_imports_strs: Iterable[
        tuple[str | tuple[str, dict[str, Any]], str | tuple[str, dict[str, Any]]]
    ]
    | None,
    backend: str | None = None,
    backend_kwargs: dict | None = None,
    config: Any = None,
) -> list[AsyncIOBaseNotificationAdapter]:
    default_adapters = []
    app_settings = NotificationSettings(config)
    adapters_imports_strs_with_default = (
        adapters_imports_strs
        if adapters_imports_strs is not None
        else app_settings.NOTIFICATION_ADAPTERS
    )
    for adapter_import_str, template_renderer_import_str in adapters_imports_strs_with_default:
        adapter_kwargs: dict = {}
        if isinstance(adapter_import_str, tuple):
            adapter_import_str, adapter_kwargs = adapter_import_str
        try:
            adapter_cls = _import_class(adapter_import_str)
        except (ImportError, ModuleNotFoundError) as e:
            raise ValueError(
                f"Notifications Adapter Error: Could not import {adapter_import_str}"
            ) from e

        try:
            adapter = adapter_cls(
                template_renderer_import_str,
                backend if backend else app_settings.NOTIFICATION_BACKEND,
                backend_kwargs,
                config,
                **adapter_kwargs,
            )
        except Exception as e:  # noqa: BLE001
            raise ValueError(
                f"Notifications Adapter Error: Could not instantiate {adapter_import_str}"
            ) from e

        if not isinstance(adapter, AsyncIOBaseNotificationAdapter):
            raise ValueError(
                f"Notifications Adapter Error: {adapter_import_str} is not a valid notification adapter"
            )

        default_adapters.append(cast(AsyncIOBaseNotificationAdapter, adapter))
    return default_adapters


def get_notification_backend(
    backend_import_str: str | None, backend_kwargs: dict | None = None, config: Any = None
) -> BaseNotificationBackend:
    app_settings = NotificationSettings(config)
    backend_import_str_with_fallback = (
        backend_import_str
        if backend_import_str is not None
        else app_settings.NOTIFICATION_BACKEND
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


def get_asyncio_notification_backend(
    backend_import_str: str | None, backend_kwargs: dict | None = None, config: Any = None
) -> AsyncIOBaseNotificationBackend:
    app_settings = NotificationSettings(config)
    backend_import_str_with_fallback = (
        backend_import_str
        if backend_import_str is not None
        else app_settings.NOTIFICATION_BACKEND
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

    if not isinstance(backend, AsyncIOBaseNotificationBackend):
        raise ValueError(
            f"Notifications Backend Error: {backend_import_str_with_fallback} is not a valid AsyncIO notification backend"
        )
    return cast(AsyncIOBaseNotificationBackend, backend)


def get_template_renderer(
    template_renderer_import_str: str | tuple[str, dict[str, Any]],
) -> BaseNotificationTemplateRenderer:
    template_renderer_kwargs: dict[str, Any] = {}
    if isinstance(template_renderer_import_str, tuple):
        template_renderer_import_str, template_renderer_kwargs = template_renderer_import_str

    try:
        template_renderer_cls = _import_class(template_renderer_import_str)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Template Renderer Error: Could not import {template_renderer_import_str}"
        ) from e

    try:
        template_renderer = template_renderer_cls(**template_renderer_kwargs)
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Template Renderer Error: Could not instantiate {template_renderer_import_str}"
        ) from e

    if not isinstance(template_renderer, BaseNotificationTemplateRenderer):
        raise ValueError(
            f"Notifications Template Renderer Error: {template_renderer_import_str} is not a valid template renderer"
        )

    return cast(BaseNotificationTemplateRenderer, template_renderer)
