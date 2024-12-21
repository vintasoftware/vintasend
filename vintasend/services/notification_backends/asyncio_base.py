import asyncio
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable

from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.dataclasses import Notification, UpdateNotificationKwargs

class AsyncIOBaseNotificationBackend(ABC):
    def __init__(self, *args, **kwargs):
        self.backend_import_str = get_class_path(self)
        self.config = kwargs.pop("config", None)
        self.backend_kwargs = kwargs

    @abstractmethod
    async def get_all_pending_notifications(self) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_all_future_notifications(self) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def persist_notification(
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
        lock: asyncio.Lock | None = None
    ) -> "Notification":
        ...

    @abstractmethod
    async def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
        lock: asyncio.Lock | None = None,
    ) -> "Notification":
        """
        Update a notification in the backend. This method should return the updated notification.
        Notifications that have already been sent should not be updated. 
        If a notification has already been sent, the method should raise a NotificationUpdateError.
        """
        ...

    @abstractmethod
    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification":
        ...

    @abstractmethod
    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification":
        ...

    @abstractmethod
    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification":
        ...

    @abstractmethod
    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
        ...

    @abstractmethod
    async def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification":
        ...

    @abstractmethod
    async def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
        ...

    @abstractmethod
    async def get_user_email_from_notification(
        self, notification_id: int | str | uuid.UUID
    ) -> str:
        ...
    
    @abstractmethod
    async def store_context_used(
        self, 
        notification_id: int | str | uuid.UUID, 
        context: dict,
        adapter_import_str: str, 
        lock: asyncio.Lock | None = None,
    ) -> None:
        ...
    