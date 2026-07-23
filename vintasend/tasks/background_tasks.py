"""Worker-side entrypoints for background notification sending.

The queue payload is the notification id and nothing else, so the worker has to build its own
`NotificationService`. Settings alone cannot do that -- a SQLAlchemy backend needs a live
session, for instance -- so the host points `NOTIFICATION_SERVICE_FACTORY` at a callable of its
own that returns a ready service. That factory is resolved and called once per worker process
and the result is reused, which means it must be safe to call once per process and the service
it returns must be safe to reuse across tasks.

The worker and the web process must read the same `NOTIFICATION_*` settings: a worker pointed
at a different backend simply will not find the notification the id refers to.
"""

import logging
import uuid
from typing import Any

from vintasend.app_settings import NotificationSettings
from vintasend.exceptions import NotificationServiceFactoryError
from vintasend.services.helpers import _import_class
from vintasend.services.notification_service import NotificationService


logger = logging.getLogger(__name__)

_cached_notification_service: "NotificationService[Any, Any] | None" = None


def get_notification_service(config: Any = None) -> "NotificationService[Any, Any]":
    """
    Return this process's notification service, building it on first use.

    The service comes from the callable named by `NOTIFICATION_SERVICE_FACTORY` and is cached
    at module scope, so the factory runs once per worker process rather than once per task.

    :param config: the host's config object, for frameworks that have no global settings.
    :raises NotificationServiceFactoryError: if the setting is unset, does not import, is not
        callable, raises when called, or returns something that is not a NotificationService.
    """
    global _cached_notification_service  # noqa: PLW0603

    if _cached_notification_service is not None:
        return _cached_notification_service

    factory_import_str = NotificationSettings(config).NOTIFICATION_SERVICE_FACTORY

    # get_config() returns {} rather than None when no framework is detected, which is the
    # ordinary case in a plain Python worker, so anything that is not a non-empty string
    # counts as unset.
    if not isinstance(factory_import_str, str) or not factory_import_str:
        raise NotificationServiceFactoryError(
            "Notification Service Factory Error: NOTIFICATION_SERVICE_FACTORY is not set, so "
            "this process cannot build a notification service to deliver notifications with"
        )

    try:
        factory = _import_class(factory_import_str)
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError) as e:
        raise NotificationServiceFactoryError(
            f"Notification Service Factory Error: Could not import {factory_import_str}"
        ) from e

    if not callable(factory):
        raise NotificationServiceFactoryError(
            f"Notification Service Factory Error: {factory_import_str} is not callable"
        )

    try:
        service = factory()
    except Exception as e:  # noqa: BLE001
        raise NotificationServiceFactoryError(
            f"Notification Service Factory Error: Could not call {factory_import_str}"
        ) from e

    if not isinstance(service, NotificationService):
        raise NotificationServiceFactoryError(
            f"Notification Service Factory Error: {factory_import_str} did not return a "
            "NotificationService"
        )

    _cached_notification_service = service
    return service


def _reset_notification_service_cache() -> None:
    """Drop the cached service so the next call rebuilds it. For tests."""
    global _cached_notification_service  # noqa: PLW0603

    _cached_notification_service = None


def send_notification(
    notification_id: int | str | uuid.UUID,
    notification_service: "NotificationService[Any, Any] | None" = None,
) -> None:
    """
    Deliver one notification from a worker, given only its id.

    This is the function a host registers as its queue task (for example
    `celery_app.task(send_notification)`), so it takes the notification id alone and resolves
    everything else from the process's service.

    Nothing is raised out of here: a task that raises can poison a worker, and the failure has
    already been recorded against the notification by `delayed_send`.

    :param notification_id: the id carried by the queue message.
    :param notification_service: an explicit service, for hosts that would rather wire it
        themselves than go through NOTIFICATION_SERVICE_FACTORY.
    """
    try:
        service = (
            notification_service if notification_service is not None else get_notification_service()
        )
        service.delayed_send(notification_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("Error sending notification %s: %s", notification_id, e)
