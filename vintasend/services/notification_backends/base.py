import datetime
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Unpack


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        UpdateNotificationKwargs,
    )


class BaseNotificationBackend(ABC):
    backend_kwargs: dict

    class Meta:
        abstract = True

    @abstractmethod
    def get_all_pending_notifications(self) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_pending_notifications(self, page: int, page_size: int) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_all_future_notifications(self) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_future_notifications(self, page: int, page_size: int) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_all_future_notifications_from_user(self, user_id: int | str | uuid.UUID) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_future_notifications_from_user(self, user_id: int | str | uuid.UUID, page: int, page_size: int) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def persist_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
    ) -> "Notification":
        raise NotImplementedError

    @abstractmethod
    def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
    ) -> "Notification":
        """
        Update a notification in the backend. This method should return the updated notification.
        Notifications that have already been sent should not be updated. If a notification has already been sent,
        the method should raise a NotificationUpdateError.
        """
        raise NotImplementedError

    @abstractmethod
    def mark_pending_as_sent(self, notification_id: int | str | uuid.UUID) -> "Notification":
        raise NotImplementedError

    @abstractmethod
    def mark_pending_as_failed(self, notification_id: int | str | uuid.UUID) -> "Notification":
        raise NotImplementedError

    @abstractmethod
    def mark_sent_as_read(self, notification_id: int | str | uuid.UUID) -> "Notification":
        raise NotImplementedError

    @abstractmethod
    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification":
        raise NotImplementedError

    @abstractmethod
    def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        raise NotImplementedError
    
    @abstractmethod
    def store_context_used(self, notification_id: int | str | uuid.UUID, context: dict) -> None:
        raise NotImplementedError