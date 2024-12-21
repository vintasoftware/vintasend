import asyncio
import datetime
import logging
import uuid
from collections.abc import Callable, Iterable
from typing import Any, ClassVar, Coroutine, Generic, TypeGuard, TypeVar, cast

from vintasend.app_settings import NotificationSettings
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend


try:
    from typing import Unpack
except ImportError:
    from typing_extensions import Unpack

from vintasend.constants import NotificationTypes
from vintasend.exceptions import (
    NotificationContextGenerationError,
    NotificationError,
    NotificationMarkFailedError,
    NotificationMarkSentError,
    NotificationSendError,
    NotificationUpdateError,
)
from vintasend.services.dataclasses import (
    Notification,
    NotificationContextDict,
    UpdateNotificationKwargs,
)
from vintasend.services.helpers import (
    get_asyncio_notification_adapters,
    get_asyncio_notification_backend,
    get_notification_adapters,
    get_notification_backend,
)
from vintasend.services.notification_adapters.async_base import (
    AsyncBaseNotificationAdapter,
    NotificationDict,
)
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.utils import get_class_path
from vintasend.utils.singleton_utils import SingletonMeta


logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None = None,
        notification_backend: B | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
    ):
        # initialize the notification settings singleton for the first time
        # to ensure all components have access to the same settings
        NotificationSettings(config)

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
            and isinstance(adapter[0], str)
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

    def send(self, notification: Notification) -> None:
        """
        Send a notification using the appropriate adapter

        This method may raise the following exceptions:
            * NotificationUserNotFoundError if the user for the notification can't be found;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification - the notification to be sent
        """
        try:
            context = self.get_notification_context(notification)
        except NotificationContextGenerationError:
            logger.exception("Failed to generate context for notification %s", notification.id)
            try:
                self.notification_backend.mark_pending_as_failed(notification.id)
            except NotificationUpdateError as e:
                raise NotificationMarkFailedError("Failed to mark notification as failed") from e
            return

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue
            try:
                adapter.send(
                    notification=notification,
                    context=context,
                )
                if isinstance(adapter, AsyncBaseNotificationAdapter):
                    return
            except Exception as e:  # noqa: BLE001
                try:
                    raise NotificationSendError("Failed to send notification") from e
                except NotificationSendError as e:
                    try:
                        self.notification_backend.mark_pending_as_failed(notification.id)
                    except NotificationUpdateError:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from e
                    raise e
            try:
                self.notification_backend.mark_pending_as_sent(notification.id)
                self.notification_backend.store_context_used(
                    notification.id, 
                    context,
                    adapter.adapter_import_str,
                )
            except NotificationUpdateError as e:
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
        """
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
    ) -> Notification:
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

    def get_all_future_notifications(self) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Returns:
            Iterable[Notification] - the future notifications
        """
        return self.notification_backend.get_all_future_notifications()

    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for

        Returns:
            Iterable[Notification] - the future notifications from the user
        """
        return self.notification_backend.get_all_future_notifications_from_user(user_id)

    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the selected page of the future notifications from the user
        """
        return self.notification_backend.get_future_notifications_from_user(
            user_id, page, page_size
        )

    def get_future_notifications(self, page: int, page_size: int) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the future notifications
        """
        return self.notification_backend.get_future_notifications(page, page_size)

    def _is_asyncio_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, NotificationContextDict]]]:
        return asyncio.iscoroutinefunction(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return not asyncio.iscoroutinefunction(context_function)

    def get_notification_context(self, notification: Notification) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification - the notification to generate the context for
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

    def get_pending_notifications(self, page: int, page_size: int) -> Iterable[Notification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the pending notifications
        """
        return self.notification_backend.get_pending_notifications(page, page_size)

    def get_notification(self, notification_id: int | str | uuid.UUID) -> Notification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get

        Returns:
            Notification - the notification
        """
        return self.notification_backend.get_notification(notification_id)

    def mark_read(self, notification_id: int | str | uuid.UUID) -> Notification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification: Notification - the notification to mark as read

        Returns:
            Notification - the updated notification
        """
        return self.notification_backend.mark_sent_as_read(notification_id)

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

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notifictaion_id: int | str | uuid.UUID - the ID of the notification to cancel
        """
        return self.notification_backend.cancel_notification(notification_id)

    def delayed_send(self, notification_dict: NotificationDict, context_dict: dict) -> None:
        """
        Send a notification using the appropriate adapter with a delay.

        This method may raise the following exceptions:
            * NotificationUserNotFoundError if the user for the notification can't be found;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_dict: dict - the notification to be sent
            context_dict: dict - the context to generate the context for the notification
        """
        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification_dict.get("notification_type"):
                continue

            if not isinstance(adapter, AsyncBaseNotificationAdapter):
                return None

            async_adapter = cast(AsyncBaseNotificationAdapter, adapter)
            try:
                async_adapter.delayed_send(
                    notification_dict=notification_dict, context_dict=context_dict
                )
            except Exception as e:  # noqa: BLE001
                try:
                    raise NotificationSendError("Failed to send notification") from e
                except NotificationSendError as e:
                    try:
                        self.notification_backend.mark_pending_as_failed(notification_dict["id"])
                    except NotificationUpdateError:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from e
                    raise e
            try:
                self.notification_backend.mark_pending_as_sent(notification_dict["id"])
                self.notification_backend.store_context_used(
                    notification_dict["id"], 
                    context_dict,
                    async_adapter.adapter_import_str,
                )
            except NotificationUpdateError as e:
                raise NotificationMarkSentError("Failed to mark notification as sent") from e


AAIO = TypeVar("AAIO", bound=AsyncIOBaseNotificationAdapter)
BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class AsyncIONotificationService(Generic[AAIO, BAIO]):
    notification_adapters: Iterable[AAIO]
    notification_backend: BAIO

    def __init__(
        self,
        notification_adapters: Iterable[AAIO] | Iterable[tuple[str, str]] | None = None,
        notification_backend: BAIO | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
    ):
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
        self.notification_adapters_import_strs = [
            (get_class_path(adapter), get_class_path(adapter.template_renderer))
            for adapter in self.notification_adapters
        ]

    def _check_is_base_notification_adapter_iterable(
        self, notification_adapters: Iterable[AAIO] | Iterable[tuple[str, str]] | None
    ) -> TypeGuard[Iterable[AAIO]]:
        return notification_adapters is not None and all(
            isinstance(adapter, AsyncIOBaseNotificationAdapter) for adapter in notification_adapters
        )

    def _check_is_adapters_tuple_iterable(
        self, notification_adapters: Iterable[AAIO] | Iterable[tuple[str, str]] | None
    ) -> TypeGuard[Iterable[tuple[str, str]]]:
        return notification_adapters is not None and all(
            (isinstance(adapter, tuple) or isinstance(adapter, list))
            and len(adapter) == 2
            and isinstance(adapter[0], str)
            and isinstance(adapter[1], str)
            for adapter in notification_adapters
        )

    async def send(self, notification: Notification, lock: asyncio.Lock | None = None) -> None:
        """
        Send a notification using the appropriate adapter

        This method may raise the following exceptions:
            * NotificationUserNotFoundError if the user for the notification can't be found;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification - the notification to be sent
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
                    notification.id, 
                    context, 
                    adapter.adapter_import_str,
                    lock
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
        """
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
    ) -> Notification:
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

    async def get_all_future_notifications(self) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Returns:
            Iterable[Notification] - the future notifications
        """
        return await self.notification_backend.get_all_future_notifications()

    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for

        Returns:
            Iterable[Notification] - the future notifications from the user
        """
        return await self.notification_backend.get_all_future_notifications_from_user(user_id)

    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the selected page of the future notifications from the user
        """
        return await self.notification_backend.get_future_notifications_from_user(
            user_id, page, page_size
        )

    async def get_future_notifications(self, page: int, page_size: int) -> Iterable[Notification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the future notifications
        """
        return await self.notification_backend.get_future_notifications(page, page_size)

    async def get_notification_context(self, notification: Notification) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification - the notification to generate the context for
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
        return asyncio.iscoroutinefunction(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return not asyncio.iscoroutinefunction(context_function)

    async def _send_notification_with_error_logging(
        self, notification: "Notification", lock: asyncio.Lock | None = None
    ) -> None:
        try:
            await self.send(notification, lock)
        except NotificationSendError:
            logger.exception("Failed to send notification %s", notification.id)
        except NotificationMarkFailedError:
            logger.exception("Failed to send notification %s", notification.id)
            logger.exception("Failed to mark notification %s as failed", notification.id)
        except NotificationMarkSentError:
            logger.info("Notification %s sent", notification.id)
            logger.exception("Failed to mark notification %s as sent", notification.id)
        else:
            logger.info("Notification %s sent", notification.id)

    async def send_pending_notifications(self) -> None:
        """
        Send all pending notifications in the backend.

        This method doesn't raise any exceptions, but it provides specific logs for each
        notification (success or failure) and a summary at the end with the number of notifications
        sent and failed.
        """

        pending_notifications = await self.notification_backend.get_all_pending_notifications()
        lock = asyncio.Lock()
        await asyncio.gather(
            *[
                self._send_notification_with_error_logging(notification, lock)
                for notification in pending_notifications
            ]
        )
        return None

    async def get_pending_notifications(self, page: int, page_size: int) -> Iterable[Notification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page

        Returns:
            Iterable[Notification] - the pending notifications
        """
        return await self.notification_backend.get_pending_notifications(page, page_size)

    async def get_notification(self, notification_id: int | str | uuid.UUID) -> Notification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get

        Returns:
            Notification - the notification
        """
        return await self.notification_backend.get_notification(notification_id)

    async def mark_read(self, notification_id: int | str | uuid.UUID) -> Notification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification: Notification - the notification to mark as read

        Returns:
            Notification - the updated notification
        """
        return await self.notification_backend.mark_sent_as_read(notification_id)

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

    async def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notifictaion_id: int | str | uuid.UUID - the ID of the notification to cancel
        """
        return await self.notification_backend.cancel_notification(notification_id)
