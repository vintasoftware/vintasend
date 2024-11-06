import datetime
import logging
import uuid
from collections.abc import Callable, Iterable
from typing import Any, ClassVar, cast

from vintasend.utils.singleton_utils import SingletonMeta
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
from vintasend.services.helpers import get_notification_adapters, get_notification_backend
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter


logger = logging.getLogger(__name__)


class Contexts(metaclass=SingletonMeta):
    _contexts: ClassVar[dict] = {}

    def register_function(self, key: str, func: Callable[[Any], NotificationContextDict]):
        self._contexts[key] = func

    def get_function(self, key: str):
        return self._contexts.get(key)


def register_context(key: str):
    def decorator(func: Callable[[Any], NotificationContextDict]):
        contexts = Contexts()
        contexts.register_function(key, func)
        return func

    return decorator


class NotificationService:
    def __init__(
        self,
        notification_adapters: Iterable[tuple[str, str]] | None = None,
        notification_backend: str | None = None,
        notification_backend_kwargs: dict | None = None,
    ):
        self.notification_adapters_import_strs = notification_adapters
        self.notification_backend_import_str = notification_backend
        self.notification_adapters = get_notification_adapters(
            notification_adapters, notification_backend, notification_backend_kwargs
        )
        self.notification_backend = get_notification_backend(
            notification_backend, notification_backend_kwargs
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
            return

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue
            try:
                adapter.send(
                    notification=notification,
                    context=context,
                )
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
            except NotificationUpdateError as e:
                raise NotificationMarkSentError("Failed to mark notification as sent") from e

    def create_notification(
        self,
        user_id: uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
    ) -> Notification:
        """
        Create a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            user_id: uuid.UUID - the user ID to send the notification to
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
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(tz=datetime.timezone.utc):
            self.send(notification)
        return notification

    def update_notification(
        self,
        notification_id: uuid.UUID,
        **kwargs: UpdateNotificationKwargs,
    ) -> Notification:
        """
        Update a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: uuid.UUID - the ID of the notification to update
            **kwargs: UpdateNotificationKwargs - the fields to update
        """
        notification = self.notification_backend.persist_notification_update(
            notification_id=notification_id,
            **kwargs,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(tz=datetime.timezone.utc):
            self.send(notification)
        return notification

    def get_notification_context(self, notification: Notification) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification - the notification to generate the context for
        """
        try:
            return Contexts().get_function(notification.context_name)(**notification.context_kwargs)
        except Exception as e:  # noqa: BLE001
            raise NotificationContextGenerationError("Failed sending notification") from e

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

    def get_notification(self, notification_id: uuid.UUID) -> Notification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: uuid.UUID - the ID of the notification to get

        Returns:
            Notification - the notification
        """
        return self.notification_backend.get_notification(notification_id)

    def mark_read(self, notification_id: uuid.UUID) -> Notification:
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
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 10,
    ) -> Iterable[Notification]:
        """
        Get unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: uuid.UUID - the user ID to get the notifications for
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

    def cancel_notification(self, notification: Notification) -> None:
        """
        Cancel a notification.

        Parameters:
            notification: Notification - the notification to cancel
        """
        return self.notification_backend.cancel_notification(notification.id)

    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
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
            if adapter.notification_type != notification_dict.get("notification_type"):
                continue
            # adapter might have a dynamic inheritance, so we need to check if it has the delayed_send method
            # instead of using isinstance
            if hasattr(adapter, "delayed_send"):
                adapter = cast(AsyncBaseNotificationAdapter, adapter)
                adapter.delayed_send(notification_dict=notification_dict, context_dict=context_dict)
