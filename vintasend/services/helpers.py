from collections.abc import Iterable
from typing import Any, cast

from vintasend.app_settings import NotificationSettings
from vintasend.exceptions import (
    NotificationQueueServiceMissingError,
    NotificationQueueServiceResolutionError,
)
from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
from vintasend.services.attachment_managers.base import BaseAttachmentManager
from vintasend.services.git_commit_sha_providers.asyncio_base import (
    AsyncIOBaseGitCommitShaProvider,
)
from vintasend.services.git_commit_sha_providers.base import BaseGitCommitShaProvider
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService
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
        backend_import_str if backend_import_str is not None else app_settings.NOTIFICATION_BACKEND
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
        backend_import_str if backend_import_str is not None else app_settings.NOTIFICATION_BACKEND
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


def get_notification_queue_service(
    queue_service_import_str: str | None,
    queue_service_kwargs: dict | None = None,
    config: Any = None,
) -> BaseNotificationQueueService:
    app_settings = NotificationSettings(config)
    queue_service_import_str_with_fallback = (
        queue_service_import_str
        if queue_service_import_str is not None
        else app_settings.NOTIFICATION_QUEUE_SERVICE
    )

    if (
        not isinstance(queue_service_import_str_with_fallback, str)
        or not queue_service_import_str_with_fallback
    ):
        raise NotificationQueueServiceMissingError(
            "Notifications Queue Service Error: no queue service import string was provided and "
            "NOTIFICATION_QUEUE_SERVICE is not set"
        )

    try:
        queue_service_cls = _import_class(queue_service_import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: Could not import {queue_service_import_str_with_fallback}"
        ) from e

    try:
        queue_service = (
            queue_service_cls(**queue_service_kwargs)
            if queue_service_kwargs
            else queue_service_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: Could not instantiate {queue_service_import_str_with_fallback}"
        ) from e

    if not isinstance(queue_service, BaseNotificationQueueService):
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: {queue_service_import_str_with_fallback} is not a valid notification queue service"
        )
    return cast(BaseNotificationQueueService, queue_service)


def get_asyncio_notification_queue_service(
    queue_service_import_str: str | None,
    queue_service_kwargs: dict | None = None,
    config: Any = None,
) -> AsyncIOBaseNotificationQueueService:
    app_settings = NotificationSettings(config)
    queue_service_import_str_with_fallback = (
        queue_service_import_str
        if queue_service_import_str is not None
        else app_settings.NOTIFICATION_QUEUE_SERVICE
    )

    if (
        not isinstance(queue_service_import_str_with_fallback, str)
        or not queue_service_import_str_with_fallback
    ):
        raise NotificationQueueServiceMissingError(
            "Notifications Queue Service Error: no queue service import string was provided and "
            "NOTIFICATION_QUEUE_SERVICE is not set"
        )

    try:
        queue_service_cls = _import_class(queue_service_import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: Could not import {queue_service_import_str_with_fallback}"
        ) from e

    try:
        queue_service = (
            queue_service_cls(**queue_service_kwargs)
            if queue_service_kwargs
            else queue_service_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: Could not instantiate {queue_service_import_str_with_fallback}"
        ) from e

    if not isinstance(queue_service, AsyncIOBaseNotificationQueueService):
        raise NotificationQueueServiceResolutionError(
            f"Notifications Queue Service Error: {queue_service_import_str_with_fallback} is not a valid AsyncIO notification queue service"
        )
    return cast(AsyncIOBaseNotificationQueueService, queue_service)


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


def get_attachment_manager(
    attachment_manager_import_str: str | None,
    attachment_manager_kwargs: dict | None = None,
    config: Any = None,
) -> BaseAttachmentManager | None:
    app_settings = NotificationSettings(config)
    import_str_with_fallback = (
        attachment_manager_import_str
        if attachment_manager_import_str is not None
        else app_settings.NOTIFICATION_ATTACHMENT_MANAGER
    )

    if not import_str_with_fallback:
        return None

    try:
        attachment_manager_cls = _import_class(import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Attachment Manager Error: Could not import {import_str_with_fallback}"
        ) from e

    try:
        attachment_manager = (
            attachment_manager_cls(**attachment_manager_kwargs)
            if attachment_manager_kwargs
            else attachment_manager_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Attachment Manager Error: Could not instantiate {import_str_with_fallback}"
        ) from e

    if not isinstance(attachment_manager, BaseAttachmentManager):
        raise ValueError(
            f"Notifications Attachment Manager Error: {import_str_with_fallback} is not a valid attachment manager"
        )
    return cast(BaseAttachmentManager, attachment_manager)


def get_asyncio_attachment_manager(
    attachment_manager_import_str: str | None,
    attachment_manager_kwargs: dict | None = None,
    config: Any = None,
) -> AsyncIOBaseAttachmentManager | None:
    app_settings = NotificationSettings(config)
    import_str_with_fallback = (
        attachment_manager_import_str
        if attachment_manager_import_str is not None
        else app_settings.NOTIFICATION_ATTACHMENT_MANAGER
    )

    if not import_str_with_fallback:
        return None

    try:
        attachment_manager_cls = _import_class(import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Attachment Manager Error: Could not import {import_str_with_fallback}"
        ) from e

    try:
        attachment_manager = (
            attachment_manager_cls(**attachment_manager_kwargs)
            if attachment_manager_kwargs
            else attachment_manager_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Attachment Manager Error: Could not instantiate {import_str_with_fallback}"
        ) from e

    if not isinstance(attachment_manager, AsyncIOBaseAttachmentManager):
        raise ValueError(
            f"Notifications Attachment Manager Error: {import_str_with_fallback} is not a valid AsyncIO attachment manager"
        )
    return cast(AsyncIOBaseAttachmentManager, attachment_manager)


def get_git_commit_sha_provider(
    git_commit_sha_provider_import_str: str | None,
    git_commit_sha_provider_kwargs: dict | None = None,
    config: Any = None,
) -> BaseGitCommitShaProvider | None:
    app_settings = NotificationSettings(config)
    import_str_with_fallback = (
        git_commit_sha_provider_import_str
        if git_commit_sha_provider_import_str is not None
        else app_settings.NOTIFICATION_GIT_COMMIT_SHA_PROVIDER
    )

    if not import_str_with_fallback:
        return None

    try:
        git_commit_sha_provider_cls = _import_class(import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: Could not import {import_str_with_fallback}"
        ) from e

    try:
        git_commit_sha_provider = (
            git_commit_sha_provider_cls(**git_commit_sha_provider_kwargs)
            if git_commit_sha_provider_kwargs
            else git_commit_sha_provider_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: Could not instantiate {import_str_with_fallback}"
        ) from e

    if not isinstance(git_commit_sha_provider, BaseGitCommitShaProvider):
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: {import_str_with_fallback} is not a valid git commit sha provider"
        )
    return cast(BaseGitCommitShaProvider, git_commit_sha_provider)


def get_asyncio_git_commit_sha_provider(
    git_commit_sha_provider_import_str: str | None,
    git_commit_sha_provider_kwargs: dict | None = None,
    config: Any = None,
) -> AsyncIOBaseGitCommitShaProvider | None:
    app_settings = NotificationSettings(config)
    import_str_with_fallback = (
        git_commit_sha_provider_import_str
        if git_commit_sha_provider_import_str is not None
        else app_settings.NOTIFICATION_GIT_COMMIT_SHA_PROVIDER
    )

    if not import_str_with_fallback:
        return None

    try:
        git_commit_sha_provider_cls = _import_class(import_str_with_fallback)
    except (ImportError, ModuleNotFoundError) as e:
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: Could not import {import_str_with_fallback}"
        ) from e

    try:
        git_commit_sha_provider = (
            git_commit_sha_provider_cls(**git_commit_sha_provider_kwargs)
            if git_commit_sha_provider_kwargs
            else git_commit_sha_provider_cls()
        )
    except Exception as e:  # noqa: BLE001
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: Could not instantiate {import_str_with_fallback}"
        ) from e

    if not isinstance(git_commit_sha_provider, AsyncIOBaseGitCommitShaProvider):
        raise ValueError(
            f"Notifications Git Commit Sha Provider Error: {import_str_with_fallback} is not a valid AsyncIO git commit sha provider"
        )
    return cast(AsyncIOBaseGitCommitShaProvider, git_commit_sha_provider)
