import asyncio
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable

from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationAttachment,
        OneOffNotification,
        UpdateNotificationKwargs,
    )


class AsyncIOBaseNotificationBackend(ABC):
    def __init__(self, *args, **kwargs):
        self.backend_import_str = get_class_path(self)
        self.config = kwargs.pop("config", None)
        self.backend_kwargs = kwargs

    @abstractmethod
    async def get_all_pending_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_all_future_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

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
        attachments: list["NotificationAttachment"] | None = None,
        tenant: str | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "Notification": ...

    @abstractmethod
    async def persist_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
        adapter_extra_parameters: dict | None = None,
        attachments: list["NotificationAttachment"] | None = None,
        tenant: str | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "OneOffNotification": ...

    @abstractmethod
    async def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
        lock: asyncio.Lock | None = None,
    ) -> "Notification | OneOffNotification":
        """
        Update a notification in the backend. This method should return the updated notification.
        Notifications that have already been sent should not be updated.
        If a notification has already been sent, the method should raise a NotificationUpdateError.
        """
        ...

    @abstractmethod
    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a pending notification as sent. Implementations must set ``sent_at`` to
        the current time on the affected row.
        """
        ...

    @abstractmethod
    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification": ...

    @abstractmethod
    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a sent notification as read. Implementations must set ``read_at`` to
        the current time on the affected row.
        """
        ...

    @abstractmethod
    async def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
        lock: asyncio.Lock | None = None,
    ) -> Iterable["Notification"]:
        """
        Mark multiple notifications as read at once.

        Semantics:
            * Every notification in ``notification_ids`` that is currently SENT is
              moved to READ, and ``read_at`` is set to the current time on every
              row moved this way.
            * If ``user_id`` is provided, the update is scoped to that user; rows
              owned by other users are never touched. This is the safe default for
              an endpoint and callers are strongly encouraged to always pass it.
            * Idempotent: ids that are already READ cause no error and their
              ``read_at`` is left untouched.
            * Returns the serialized notifications for the requested ids that are
              READ after the operation (newly-marked + already-read), so the caller
              sees the final state. Ids that are missing, not owned, or in a
              non-SENT-non-READ state are omitted from the result.

        Unlike ``mark_sent_as_read``, this method NEVER raises when zero rows are
        updated -- it is idempotent by construction.
        """
        ...

    @abstractmethod
    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None: ...

    @abstractmethod
    async def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification | OneOffNotification": ...

    @abstractmethod
    async def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]: ...

    @abstractmethod
    async def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]: ...

    @abstractmethod
    async def filter_all_in_app_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """
        Return all in-app notifications (read + unread) for a user, unpaginated.

        "All" means ``notification_type == IN_APP`` AND ``status in (SENT, READ)``;
        internal pipeline states (PENDING_SEND, FAILED, CANCELLED) are excluded.

        Prefer the paginated ``filter_in_app_notifications`` + ``count_in_app_notifications``
        for end-user facing listings; this unpaginated variant is meant for internal
        and count use.
        """
        ...

    @abstractmethod
    async def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> Iterable["Notification"]:
        """
        Return a page of in-app notifications (read + unread) for a user.

        Same SENT/READ filtering as ``filter_all_in_app_notifications``.
        """
        ...

    async def count_in_app_notifications(self, user_id: int | str | uuid.UUID) -> int:
        """
        Total number of in-app notifications (read + unread) for a user.

        Concrete default derived from ``filter_all_in_app_notifications`` so existing
        backends keep working without changes. Backends SHOULD override this for
        efficiency (e.g. a database ``COUNT``).
        """
        return sum(1 for _ in await self.filter_all_in_app_notifications(user_id))

    async def count_in_app_unread_notifications(self, user_id: int | str | uuid.UUID) -> int:
        """
        Total number of unread in-app notifications for a user.

        Concrete default derived from ``filter_all_in_app_unread_notifications`` so
        existing backends keep working without changes. Backends SHOULD override this
        for efficiency (e.g. a database ``COUNT``).
        """
        return sum(1 for _ in await self.filter_all_in_app_unread_notifications(user_id))

    @abstractmethod
    async def get_user_email_from_notification(
        self, notification_id: int | str | uuid.UUID
    ) -> str: ...

    @abstractmethod
    async def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
        lock: asyncio.Lock | None = None,
    ) -> None: ...
