import asyncio
import datetime
import logging
import sys
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any, ClassVar, Coroutine, Generic, TypeGuard, TypeVar, cast

from vintasend.app_settings import NotificationSettings
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend


# `typing.Unpack` landed in 3.11; `typing_extensions` is a hard runtime dependency so the
# fallback is always importable. Use a `sys.version_info` guard rather than try/except
# ImportError: mypy evaluates the former statically, and without it the `Unpack[...]`
# annotations silently degrade to `dict[str, Any]` when type-checking against py310.
if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import (
    DuplicateNotificationAdapterError,
    NotificationContextGenerationError,
    NotificationError,
    NotificationMarkFailedError,
    NotificationMarkSentError,
    NotificationQueueServiceMissingError,
    NotificationSendError,
    NotificationUpdateError,
)
from vintasend.services.dataclasses import (
    Notification,
    NotificationAttachment,
    NotificationContextDict,
    OneOffNotification,
    UpdateNotificationKwargs,
)
from vintasend.services.helpers import (
    get_asyncio_notification_adapters,
    get_asyncio_notification_backend,
    get_notification_adapters,
    get_notification_backend,
    get_notification_queue_service,
)
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService
from vintasend.services.service_utils import (
    download_from_url,
    is_asyncio_context_function,
    is_sync_context_function,
    is_url,
    read_file_data,
    validate_attachments,
    validate_email_or_phone,
)
from vintasend.services.utils import get_class_path
from vintasend.utils.singleton_utils import SingletonMeta


logger = logging.getLogger(__name__)

# Queue delivery is at-least-once, so a worker can be handed the same notification id twice.
# A notification in one of these statuses has already had its outcome decided, and re-sending
# it would mean a duplicate the recipient can see. PENDING_SEND and FAILED are deliberately
# absent: re-enqueueing a failed notification is how a host retries it.
ALREADY_DELIVERED_NOTIFICATION_STATUSES = frozenset(
    {
        NotificationStatus.SENT.value,
        NotificationStatus.READ.value,
        NotificationStatus.CANCELLED.value,
    }
)


def validate_unique_adapter_notification_types(
    adapters: Iterable[BaseNotificationAdapter | AsyncIOBaseNotificationAdapter],
) -> None:
    """
    Validate that no two adapters declare the same notification type.

    :param adapters: An iterable of notification adapters.
    :raises DuplicateNotificationAdapterError: If duplicate notification types are found.
    """
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for adapter in adapters:
        grouped[adapter.notification_type.value].append(adapter.adapter_import_str)

    duplicates = [
        f"{notification_type} ({', '.join(import_strs)})"
        for notification_type, import_strs in grouped.items()
        if len(import_strs) > 1
    ]

    if duplicates:
        raise DuplicateNotificationAdapterError(
            "Duplicate adapter notification types are not allowed. Found duplicates for: "
            + ", ".join(duplicates)
        )


class Contexts(metaclass=SingletonMeta):
    _contexts: ClassVar[
        dict[
            str,
            Callable[[Any], NotificationContextDict]
            | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
        ]
    ] = {}

    def register_function(
        self,
        key: str,
        func: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ):
        self._contexts[key] = func

    def get_function(self, key: str):
        return self._contexts.get(key)


def register_context(key: str):
    def decorator(func: Callable[[Any], NotificationContextDict]):
        contexts = Contexts()
        contexts.register_function(key, func)
        return func

    return decorator


A = TypeVar("A", BaseNotificationAdapter, AsyncBaseNotificationAdapter)
B = TypeVar("B", bound=BaseNotificationBackend)


class NotificationService(Generic[A, B]):
    notification_adapters: Iterable[A]
    notification_backend: B
    notification_queue_service: BaseNotificationQueueService | None
    raise_on_failed_send: bool

    def __init__(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None = None,
        notification_backend: B | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
        notification_queue_service: BaseNotificationQueueService | str | None = None,
        raise_on_failed_send: bool = False,
    ):
        """
        Build a notification service.

        :param notification_adapters: adapter instances, or (adapter, renderer) import strings.
        :param notification_backend: a backend instance or its import string.
        :param notification_backend_kwargs: kwargs for the backend when it is an import string.
        :param config: the host's config object, used by FastAPI-style apps.
        :param notification_queue_service: the queue service used to hand background
            notifications to a worker. Accepts an instance or an import string; when it is
            None, `NOTIFICATION_QUEUE_SERVICE` is used, and background sending is simply
            unavailable if that is unset too.
        :param raise_on_failed_send: when False (the default), a failure to send, enqueue, or
            record a notification's outcome is logged and the remaining adapters still run.
            When True, those failures are raised, which is the 1.x behaviour.
        """
        # initialize the notification settings singleton for the first time
        # to ensure all components have access to the same settings
        NotificationSettings(config)

        self.raise_on_failed_send = raise_on_failed_send

        if isinstance(notification_queue_service, BaseNotificationQueueService):
            self.notification_queue_service = notification_queue_service
        else:
            try:
                self.notification_queue_service = get_notification_queue_service(
                    notification_queue_service, None, config
                )
            except NotificationQueueServiceMissingError:
                # Nothing configured at all: background sending stays unavailable, which
                # only matters once send() meets an AsyncBaseNotificationAdapter. A
                # NotificationQueueServiceResolutionError -- configured but unusable, e.g. a
                # typo'd import string -- deliberately propagates instead: swallowing it
                # would read as "no queue configured" and silently never deliver.
                self.notification_queue_service = None

        if isinstance(notification_backend, BaseNotificationBackend):
            self.notification_backend = cast(B, notification_backend)
        else:
            self.notification_backend = cast(
                B,
                get_notification_backend(notification_backend, notification_backend_kwargs, config),
            )
        self.notification_backend_import_str = get_class_path(self.notification_backend)

        if notification_adapters is None or self._check_is_adapters_tuple_iterable(
            notification_adapters
        ):
            self.notification_adapters = cast(
                Iterable[A],
                get_notification_adapters(
                    notification_adapters,
                    self.notification_backend_import_str,
                    notification_backend_kwargs if notification_backend_kwargs is not None else {},
                    config,
                ),
            )
        elif self._check_is_base_notification_adapter_iterable(notification_adapters):
            self.notification_adapters = notification_adapters
        else:
            raise NotificationError("Invalid notification adapters")

        validate_unique_adapter_notification_types(self.notification_adapters)

        self.notification_adapters_import_strs = [
            (get_class_path(adapter), get_class_path(adapter.template_renderer))
            for adapter in self.notification_adapters
        ]

    def _check_is_base_notification_adapter_iterable(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[A]]:
        return notification_adapters is not None and all(
            isinstance(adapter, BaseNotificationAdapter) for adapter in notification_adapters
        )

    def _check_is_adapters_tuple_iterable(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]]:
        return notification_adapters is not None and all(
            (isinstance(adapter, tuple) or isinstance(adapter, list))
            and len(adapter) == 2
            and (
                isinstance(adapter[0], str)
                or (
                    isinstance(adapter[0], tuple)
                    and isinstance(adapter[0][0], str)
                    and isinstance(adapter[0][1], dict)
                )
            )
            and (
                isinstance(adapter[1], str)
                or (
                    isinstance(adapter[1], tuple)
                    and isinstance(adapter[1][0], str)
                    and isinstance(adapter[1][1], dict)
                )
            )
            for adapter in notification_adapters
        )

    def _validate_attachments(
        self, attachments: list[NotificationAttachment]
    ) -> list[NotificationAttachment]:
        """Validate attachments and return the validated list."""
        return validate_attachments(attachments)

    def _read_file_data(self, file) -> bytes:
        """Read file data from a path, URL, `Path` object, or file-like object."""
        return read_file_data(file)

    def _is_url(self, file_str: str) -> bool:
        """Check whether a string is a URL rather than a local file path."""
        return is_url(file_str)

    def _download_from_url(self, url: str) -> bytes:
        """Download file content from a URL."""
        return download_from_url(url)

    def register_queue_service(self, queue_service: BaseNotificationQueueService) -> None:
        """
        Inject the queue service after construction.

        Useful when the queue service cannot exist yet at construction time -- a broker
        connection built during application startup, for example.

        Parameters:
            queue_service: BaseNotificationQueueService - the queue service to use for
                background sends from now on
        """
        self.notification_queue_service = queue_service

    def send(self, notification: Notification | OneOffNotification) -> None:
        """
        Send a notification using the appropriate adapter.

        Adapters that subclass AsyncBaseNotificationAdapter are not delivered here. Their
        notification id is handed to the configured queue service and a worker delivers it
        later through delayed_send, so no context is generated and the notification's status
        is left untouched on that path.

        With raise_on_failed_send=False (the default) every failure below is logged and the
        remaining adapters still run. With raise_on_failed_send=True this method may raise:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationQueueServiceMissingError if a background adapter has no queue
              service to enqueue through;
            * NotificationSendError if the adapter fails to send the notification, or the
              queue service fails to accept it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification | OneOffNotification - the notification to be sent
        """
        # Generated lazily, and only for adapters that deliver in this process: the enqueue
        # branch below must not pay for a context the worker will generate again anyway.
        context: NotificationContextDict | None = None

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if isinstance(adapter, AsyncBaseNotificationAdapter):
                if self.notification_queue_service is None:
                    logger.error(
                        "Cannot send notification %s in the background: adapter %s requires a "
                        "queue service and none is configured",
                        notification.id,
                        adapter.adapter_import_str,
                    )
                    if self.raise_on_failed_send:
                        raise NotificationQueueServiceMissingError(
                            "No notification queue service is configured"
                        )
                    continue
                try:
                    self.notification_queue_service.enqueue_notification(notification.id)
                except NotificationError as e:
                    # Queue services are contractually required to wrap broker failures in a
                    # NotificationError subclass. Anything they leave unwrapped escapes here,
                    # as their base class documents.
                    logger.exception("Failed to enqueue notification %s", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationSendError("Failed to enqueue notification") from e
                continue

            if context is None:
                try:
                    context = self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification.id
                    )
                    try:
                        self.notification_backend.mark_pending_as_failed(notification.id)
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification.id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    # The context belongs to the notification, not to one adapter, so no
                    # other adapter could send it either.
                    return

            try:
                adapter.send(
                    notification=notification,
                    context=context,
                )
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification.id)
                try:
                    self.notification_backend.mark_pending_as_failed(notification.id)
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                self.notification_backend.mark_pending_as_sent(notification.id)
                self.notification_backend.store_context_used(
                    notification.id,
                    context,
                    adapter.adapter_import_str,
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification.id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

    def create_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[NotificationAttachment] | None = None,
    ) -> Notification:
        """
        Create a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to send the notification to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[NotificationAttachment] | None - the list of attachments to include
        """
        validated_attachments = self._validate_attachments(attachments or [])

        notification = self.notification_backend.persist_notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=validated_attachments,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def create_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[NotificationAttachment] | None = None,
    ) -> "OneOffNotification":
        """
        Create a one-off notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * InvalidOneOffNotificationRecipientError if email_or_phone is invalid;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        """
        validate_email_or_phone(email_or_phone)
        validated_attachments = self._validate_attachments(attachments or [])

        notification = self.notification_backend.persist_one_off_notification(
            email_or_phone=email_or_phone,
            first_name=first_name,
            last_name=last_name,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=validated_attachments,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def update_notification(
        self,
        notification_id: int | str | uuid.UUID,
        **kwargs: Unpack[UpdateNotificationKwargs],
    ) -> Notification | OneOffNotification:
        """
        Update a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to update
            **kwargs: UpdateNotificationKwargs - the fields to update
        """
        notification = self.notification_backend.persist_notification_update(
            notification_id=notification_id,
            update_data=kwargs,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def get_all_future_notifications(self) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return self.notification_backend.get_all_future_notifications()

    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications from the user
        """
        return self.notification_backend.get_all_future_notifications_from_user(user_id)

    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of the future notifications from the user
        """
        return self.notification_backend.get_future_notifications_from_user(
            user_id, page, page_size
        )

    def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return self.notification_backend.get_future_notifications(page, page_size)

    def _is_asyncio_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, NotificationContextDict]]]:
        return is_asyncio_context_function(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return is_sync_context_function(context_function)

    def get_notification_context(
        self, notification: Notification | OneOffNotification
    ) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification | OneOffNotification - the notification to generate the context for
        """
        context_function = Contexts().get_function(notification.context_name)
        if context_function is None:
            raise NotificationContextGenerationError("Context function not found")
        try:
            if self._is_asyncio_context_function(context_function):
                return asyncio.run(context_function(*[], **notification.context_kwargs))
            elif self._is_sync_context_function(context_function):
                return context_function(*[], **notification.context_kwargs)
            raise NotificationContextGenerationError("Invalid context function")
        except Exception as e:  # noqa: BLE001
            raise NotificationContextGenerationError("Failed getting notification context") from e

    def send_pending_notifications(self) -> None:
        """
        Send all pending notifications in the backend.

        This method doesn't raise any exceptions, but it provides specific logs for each
        notification (success or failure) and a summary at the end with the number of notifications
        sent and failed.
        """

        pending_notifications = self.notification_backend.get_all_pending_notifications()
        notifications_sent = 0
        notifications_failed = 0
        for notification in pending_notifications:
            try:
                self.send(notification)
            except NotificationSendError:
                notifications_failed += 1
                logger.exception("Failed to send notification %s", notification.id)
            except NotificationMarkFailedError:
                notifications_failed += 1
                logger.exception("Failed to send notification %s", notification.id)
                logger.exception("Failed to mark notification %s as failed", notification.id)
            except NotificationMarkSentError:
                logger.info("Notification %s sent", notification.id)
                logger.exception("Failed to mark notification %s as sent", notification.id)
                notifications_sent += 1
            else:
                logger.info("Notification %s sent", notification.id)
                notifications_sent += 1

        logger.info("Sent %s notifications", notifications_sent)
        logger.info("Failed to send %s notifications", notifications_failed)

    def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the pending notifications
        """
        return self.notification_backend.get_pending_notifications(page, page_size)

    def get_notification(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get

        Returns:
            Notification | OneOffNotification - the notification
        """
        return self.notification_backend.get_notification(notification_id)

    def mark_read(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to mark as read

        Returns:
            Notification | OneOffNotification - the updated notification
        """
        return self.notification_backend.mark_sent_as_read(notification_id)

    def mark_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> Iterable[Notification]:
        """
        Mark multiple notifications as read at once.

        This is idempotent: ids that are already read, missing, not owned by
        ``user_id`` (when provided), or in a non-SENT state are simply skipped --
        no error is raised. When ``user_id`` is provided the update is scoped to
        that user; passing it is strongly recommended for endpoint use.

        Parameters:
            notification_ids: Iterable[int | str | uuid.UUID] - the notifications to mark as read
            user_id: int | str | uuid.UUID | None - optional owner scope

        Returns:
            Iterable[Notification] - the requested notifications that are read after the operation
        """
        return self.notification_backend.mark_sent_as_read_bulk(notification_ids, user_id=user_id)

    def get_in_app_unread(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
    ) -> Iterable[Notification]:
        """
        Get unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the unread in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self.notification_backend.filter_in_app_unread_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    def get_in_app_notifications(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
    ) -> Iterable[Notification]:
        """
        Get all in-app notifications (read + unread) for a user, paginated.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self.notification_backend.filter_in_app_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    def get_in_app_notifications_count(self, user_id: int | str | uuid.UUID) -> int:
        """
        Get the total count of in-app notifications (read + unread) for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self.notification_backend.count_in_app_notifications(user_id)

    def get_in_app_unread_count(self, user_id: int | str | uuid.UUID) -> int:
        """
        Get the total count of unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self.notification_backend.count_in_app_unread_notifications(user_id)

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to cancel
        """
        return self.notification_backend.cancel_notification(notification_id)

    def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Deliver a notification from a background worker, given only its id.

        This is the worker half of the background path: `send()` hands the notification id to
        the queue service, and the worker calls this. The notification is reloaded from the
        backend and its context is generated here, at delivery time, so a scheduled
        notification renders against current data exactly as a foreground send does.

        Delivery is at-least-once, so this may be called twice for the same id. A notification
        that has already been delivered (or cancelled) is skipped rather than sent again.

        With raise_on_failed_send=False (the default) failures are logged rather than raised.
        With raise_on_failed_send=True this method may raise:
            * NotificationNotFoundError if the id does not resolve to a notification;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if no background adapter handles the notification's type,
              or the adapter fails to send it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Note that NotificationNotFoundError propagates regardless of raise_on_failed_send:
        an id that resolves to nothing is a wiring or retention problem, not a failed send.

        Parameters:
            notification_id: int | str | uuid.UUID - the id of the notification to deliver
        """
        notification = self.notification_backend.get_notification(notification_id)

        if notification.status in ALREADY_DELIVERED_NOTIFICATION_STATUSES:
            logger.info(
                "Skipping background send of notification %s: its status is already %s",
                notification_id,
                notification.status,
            )
            return

        context: NotificationContextDict | None = None
        background_adapter_found = False

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if not isinstance(adapter, AsyncBaseNotificationAdapter):
                # A foreground adapter has nothing to do in a worker, but the ones after it
                # in the list might, so keep going.
                continue

            background_adapter_found = True

            if context is None:
                try:
                    context = self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification_id
                    )
                    try:
                        self.notification_backend.mark_pending_as_failed(notification_id)
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification_id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    return

            try:
                adapter.send(notification=notification, context=context)
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification_id)
                try:
                    self.notification_backend.mark_pending_as_failed(notification_id)
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification_id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                self.notification_backend.mark_pending_as_sent(notification_id)
                self.notification_backend.store_context_used(
                    notification_id,
                    context,
                    adapter.adapter_import_str,
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification_id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

        if not background_adapter_found:
            logger.error(
                "No background notification adapter is configured for notification %s of type %s",
                notification_id,
                notification.notification_type,
            )
            if self.raise_on_failed_send:
                raise NotificationSendError(
                    "No background notification adapter found for this notification"
                )


AAIO = TypeVar("AAIO", bound=AsyncIOBaseNotificationAdapter)
BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class AsyncIONotificationService(Generic[AAIO, BAIO]):
    notification_adapters: Iterable[AAIO]
    notification_backend: BAIO

    def __init__(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None = None,
        notification_backend: BAIO | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
    ):
        # initialize the notification settings singleton for the first time
        # to ensure all components have access to the same settings
        NotificationSettings(config)

        if isinstance(notification_backend, AsyncIOBaseNotificationBackend):
            self.notification_backend = cast(BAIO, notification_backend)
        else:
            self.notification_backend = cast(
                BAIO,
                get_asyncio_notification_backend(
                    notification_backend, notification_backend_kwargs, config
                ),
            )
        self.notification_backend_import_str = get_class_path(self.notification_backend)

        if notification_adapters is None or self._check_is_adapters_tuple_iterable(
            notification_adapters
        ):
            self.notification_adapters = cast(
                Iterable[AAIO],
                get_asyncio_notification_adapters(
                    notification_adapters,
                    self.notification_backend_import_str,
                    notification_backend_kwargs if notification_backend_kwargs is not None else {},
                    config,
                ),
            )
        elif self._check_is_base_notification_adapter_iterable(notification_adapters):
            self.notification_adapters = notification_adapters
        else:
            raise NotificationError("Invalid notification adapters")

        validate_unique_adapter_notification_types(self.notification_adapters)

        self.notification_adapters_import_strs = [
            (get_class_path(adapter), get_class_path(adapter.template_renderer))
            for adapter in self.notification_adapters
        ]

    def _check_is_base_notification_adapter_iterable(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[AAIO]]:
        return notification_adapters is not None and all(
            isinstance(adapter, AsyncIOBaseNotificationAdapter) for adapter in notification_adapters
        )

    def _check_is_adapters_tuple_iterable(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]]:
        return notification_adapters is not None and all(
            (isinstance(adapter, tuple) or isinstance(adapter, list))
            and len(adapter) == 2
            and (
                isinstance(adapter[0], str)
                or (
                    isinstance(adapter[0], tuple)
                    and isinstance(adapter[0][0], str)
                    and isinstance(adapter[0][1], dict)
                )
            )
            and (
                isinstance(adapter[1], str)
                or (
                    isinstance(adapter[1], tuple)
                    and isinstance(adapter[1][0], str)
                    and isinstance(adapter[1][1], dict)
                )
            )
            for adapter in notification_adapters
        )

    def _validate_attachments(
        self, attachments: list[NotificationAttachment]
    ) -> list[NotificationAttachment]:
        """Validate attachments and return the validated list."""
        return validate_attachments(attachments)

    def _read_file_data(self, file) -> bytes:
        """Read file data from a path, URL, `Path` object, or file-like object."""
        return read_file_data(file)

    def _is_url(self, file_str: str) -> bool:
        """Check whether a string is a URL rather than a local file path."""
        return is_url(file_str)

    def _download_from_url(self, url: str) -> bytes:
        """Download file content from a URL."""
        return download_from_url(url)

    async def send(
        self, notification: Notification | OneOffNotification, lock: asyncio.Lock | None = None
    ) -> None:
        """
        Send a notification using the appropriate adapter

        This method may raise the following exceptions:
            * NotificationUserNotFoundError if the user for the notification can't be found;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification | OneOffNotification - the notification to be sent
        """
        try:
            context = await self.get_notification_context(notification)
        except NotificationContextGenerationError:
            logger.exception("Failed to generate context for notification %s", notification.id)
            try:
                await self.notification_backend.mark_pending_as_failed(notification.id, lock)
            except NotificationUpdateError as e:
                raise NotificationMarkFailedError("Failed to mark notification as failed") from e
            return None

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue
            if not isinstance(adapter, AsyncIOBaseNotificationAdapter):
                continue
            try:
                await adapter.send(
                    notification=notification,
                    context=context,
                )
            except Exception as e:
                try:
                    raise NotificationSendError("Failed to send notification") from e
                except NotificationSendError as e:
                    try:
                        await self.notification_backend.mark_pending_as_failed(
                            notification.id, lock
                        )
                    except NotificationUpdateError:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from e
                    raise e
            try:
                await self.notification_backend.mark_pending_as_sent(notification.id, lock)
                await self.notification_backend.store_context_used(
                    notification.id, context, adapter.adapter_import_str, lock
                )
            except NotificationUpdateError as e:
                raise NotificationMarkSentError("Failed to mark notification as sent") from e
        return None

    async def create_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[NotificationAttachment] | None = None,
    ) -> Notification:
        """
        Create a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to send the notification to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[NotificationAttachment] | None - the list of attachments to include
        """
        validated_attachments = self._validate_attachments(attachments or [])

        notification = await self.notification_backend.persist_notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=validated_attachments,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def create_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[NotificationAttachment] | None = None,
    ) -> OneOffNotification:
        """
        Create a one-off notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * InvalidOneOffNotificationRecipientError if email_or_phone is invalid;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        """
        validate_email_or_phone(email_or_phone)
        validated_attachments = self._validate_attachments(attachments or [])

        notification = await self.notification_backend.persist_one_off_notification(
            email_or_phone=email_or_phone,
            first_name=first_name,
            last_name=last_name,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=validated_attachments,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def update_notification(
        self,
        notification_id: int | str | uuid.UUID,
        **kwargs: Unpack[UpdateNotificationKwargs],
    ) -> Notification | OneOffNotification:
        """
        Update a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to update
            **kwargs: UpdateNotificationKwargs - the fields to update
        """
        notification = await self.notification_backend.persist_notification_update(
            notification_id=notification_id,
            update_data=kwargs,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def get_all_future_notifications(self) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return await self.notification_backend.get_all_future_notifications()

    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications from the user
        """
        return await self.notification_backend.get_all_future_notifications_from_user(user_id)

    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of the future notifications from the user
        """
        return await self.notification_backend.get_future_notifications_from_user(
            user_id, page, page_size
        )

    async def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return await self.notification_backend.get_future_notifications(page, page_size)

    async def get_notification_context(
        self, notification: Notification | OneOffNotification
    ) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification | OneOffNotification - the notification to generate the context for
        """
        context_function = Contexts().get_function(notification.context_name)
        if context_function is None:
            raise NotificationContextGenerationError("Context function not found")
        try:
            if self._is_asyncio_context_function(context_function):
                return await context_function(*[], **notification.context_kwargs)
            elif self._is_sync_context_function(context_function):
                return context_function(*[], **notification.context_kwargs)
            raise NotificationContextGenerationError("Invalid context function")
        except Exception as e:  # noqa: BLE001
            raise NotificationContextGenerationError("Failed getting notification context") from e

    def _is_asyncio_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, NotificationContextDict]]]:
        return is_asyncio_context_function(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return is_sync_context_function(context_function)

    async def _send_notification_with_error_logging(
        self, notification: "Notification | OneOffNotification", lock: asyncio.Lock | None = None
    ) -> bool:
        """
        Send a notification, logging success or failure, and report whether it counts as sent.

        Returns:
            bool - True if the notification counts as sent, False if it counts as failed.
        """
        try:
            await self.send(notification, lock)
        except NotificationSendError:
            logger.exception("Failed to send notification %s", notification.id)
            return False
        except NotificationMarkFailedError:
            logger.exception("Failed to send notification %s", notification.id)
            logger.exception("Failed to mark notification %s as failed", notification.id)
            return False
        except NotificationMarkSentError:
            logger.info("Notification %s sent", notification.id)
            logger.exception("Failed to mark notification %s as sent", notification.id)
            return True
        else:
            logger.info("Notification %s sent", notification.id)
            return True

    async def send_pending_notifications(self) -> None:
        """
        Send all pending notifications in the backend.

        This method doesn't raise any exceptions, but it provides specific logs for each
        notification (success or failure) and a summary at the end with the number of notifications
        sent and failed.
        """

        pending_notifications = await self.notification_backend.get_all_pending_notifications()
        lock = asyncio.Lock()
        results = await asyncio.gather(
            *[
                self._send_notification_with_error_logging(notification, lock)
                for notification in pending_notifications
            ]
        )
        notifications_sent = sum(1 for result in results if result)
        notifications_failed = len(results) - notifications_sent

        logger.info("Sent %s notifications", notifications_sent)
        logger.info("Failed to send %s notifications", notifications_failed)

    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification | OneOffNotification] - the pending notifications
        """
        return await self.notification_backend.get_pending_notifications(page, page_size)

    async def get_notification(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get

        Returns:
            Notification | OneOffNotification - the notification
        """
        return await self.notification_backend.get_notification(notification_id)

    async def mark_read(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to mark as read

        Returns:
            Notification | OneOffNotification - the updated notification
        """
        return await self.notification_backend.mark_sent_as_read(notification_id)

    async def mark_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> Iterable[Notification]:
        """
        Mark multiple notifications as read at once.

        This is idempotent: ids that are already read, missing, not owned by
        ``user_id`` (when provided), or in a non-SENT state are simply skipped --
        no error is raised. When ``user_id`` is provided the update is scoped to
        that user; passing it is strongly recommended for endpoint use.

        Parameters:
            notification_ids: Iterable[int | str | uuid.UUID] - the notifications to mark as read
            user_id: int | str | uuid.UUID | None - optional owner scope

        Returns:
            Iterable[Notification] - the requested notifications that are read after the operation
        """
        return await self.notification_backend.mark_sent_as_read_bulk(
            notification_ids, user_id=user_id
        )

    async def get_in_app_unread(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
    ) -> Iterable[Notification]:
        """
        Get unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the unread in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self.notification_backend.filter_in_app_unread_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    async def get_in_app_notifications(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
    ) -> Iterable[Notification]:
        """
        Get all in-app notifications (read + unread) for a user, paginated.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self.notification_backend.filter_in_app_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    async def get_in_app_notifications_count(self, user_id: int | str | uuid.UUID) -> int:
        """
        Get the total count of in-app notifications (read + unread) for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self.notification_backend.count_in_app_notifications(user_id)

    async def get_in_app_unread_count(self, user_id: int | str | uuid.UUID) -> int:
        """
        Get the total count of unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self.notification_backend.count_in_app_unread_notifications(user_id)

    async def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to cancel
        """
        return await self.notification_backend.cancel_notification(notification_id)
