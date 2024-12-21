import datetime
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        UpdateNotificationKwargs,
    )


class BaseNotificationBackend(ABC):
    backend_import_str: str
    backend_kwargs: dict
    config: Any

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        self.backend_import_str = get_class_path(self)
        self.config = kwargs.pop("config", None)
        self.backend_kwargs = kwargs

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
    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        raise NotImplementedError

    @abstractmethod
    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
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
        adapter_extra_parameters: dict | None = None,
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
    def store_context_used(
        self, 
        notification_id: int | str | uuid.UUID, 
        context: dict,
        adapter_import_str: str,
    ) -> None:
        raise NotImplementedError
