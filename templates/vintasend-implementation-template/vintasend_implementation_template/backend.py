"""Storage seam stub.

Subclass ``BaseNotificationBackend`` (and its AsyncIO twin) to persist, query, and update
notifications against whatever storage your integration targets (an ORM, a queue, an in-memory
store, ...). Every abstract method below raises ``NotImplementedError`` with a pointer at the
real contract in ``vintasend``; replace each body with a working implementation, one at a time.
"""

import datetime
import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend


if TYPE_CHECKING:
    import asyncio

    from vintasend.services.dataclasses import (
        AnyNotificationAttachment,
        AttachmentFileRecord,
        Notification,
        OneOffNotification,
        StoredAttachment,
        UpdateNotificationKwargs,
    )
    from vintasend.services.notification_backends.filters import (
        NotificationFilter,
        NotificationOrderBy,
    )


class ImplementationTemplateBackend(BaseNotificationBackend):
    """TODO: rename and implement. See ``vintasend/services/notification_backends/base.py``."""

    def get_all_pending_notifications(self) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_pending_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_pending_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_pending_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_pending_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_all_future_notifications(self) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_future_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_future_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_future_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_future_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_future_notifications_from_user — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_future_notifications_from_user — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_future_notifications_from_user — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_future_notifications_from_user — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

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
        attachments: "list[AnyNotificationAttachment] | None" = None,
        tenant: str | None = None,
    ) -> "Notification":
        """TODO: implement persist_notification — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_notification — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def persist_one_off_notification(
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
        attachments: "list[AnyNotificationAttachment] | None" = None,
        tenant: str | None = None,
    ) -> "OneOffNotification":
        """TODO: implement persist_one_off_notification — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_one_off_notification — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
    ) -> "Notification | OneOffNotification":
        """TODO: implement persist_notification_update — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_notification_update — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_pending_as_sent — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_pending_as_sent — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_pending_as_failed — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_pending_as_failed — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_sent_as_read — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_sent_as_read — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> Iterable["Notification"]:
        """TODO: implement mark_sent_as_read_bulk — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_sent_as_read_bulk — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """TODO: implement cancel_notification — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement cancel_notification — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification | OneOffNotification":
        """TODO: implement get_notification — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_notification — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """TODO: implement filter_all_in_app_unread_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_all_in_app_unread_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
        """TODO: implement filter_in_app_unread_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_in_app_unread_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def filter_all_in_app_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """TODO: implement filter_all_in_app_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_all_in_app_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> Iterable["Notification"]:
        """TODO: implement filter_in_app_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_in_app_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def filter_notifications(
        self,
        filter: "NotificationFilter",  # noqa: A002
        page: int,
        page_size: int,
        order_by: "NotificationOrderBy | None" = None,
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement filter_notifications — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_notifications — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        """TODO: implement get_user_email_from_notification — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_user_email_from_notification — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
    ) -> None:
        """TODO: implement store_context_used — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_context_used — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def store_git_commit_sha(
        self,
        notification_id: int | str | uuid.UUID,
        git_commit_sha: str,
    ) -> None:
        """TODO: implement store_git_commit_sha — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_git_commit_sha — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def store_attachment_file_record(
        self, record: "AttachmentFileRecord"
    ) -> "AttachmentFileRecord":
        """TODO: implement store_attachment_file_record — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_attachment_file_record — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_attachment_file_record(self, file_id: str) -> "AttachmentFileRecord | None":
        """TODO: implement get_attachment_file_record — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_attachment_file_record — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def find_attachment_file_by_checksum(
        self, checksum: str, size: int
    ) -> "AttachmentFileRecord | None":
        """TODO: implement find_attachment_file_by_checksum — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement find_attachment_file_by_checksum — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def delete_attachment_file(self, file_id: str) -> None:
        """TODO: implement delete_attachment_file — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_attachment_file — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_orphaned_attachment_files(self) -> "Iterable[AttachmentFileRecord]":
        """TODO: implement get_orphaned_attachment_files — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_orphaned_attachment_files — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def get_attachments(
        self, notification_id: int | str | uuid.UUID
    ) -> "Iterable[StoredAttachment]":
        """TODO: implement get_attachments — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_attachments — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )

    def delete_notification_attachment(self, attachment_id: int | str | uuid.UUID) -> None:
        """TODO: implement delete_notification_attachment — see vintasend/services/notification_backends/base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_notification_attachment — see "
            "vintasend/services/notification_backends/base.py for the contract"
        )


class ImplementationTemplateAsyncIOBackend(AsyncIOBaseNotificationBackend):
    """TODO: rename and implement. See ``vintasend/services/notification_backends/asyncio_base.py``."""

    async def get_all_pending_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_pending_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_pending_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_pending_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_pending_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_all_future_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_future_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_future_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_future_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_future_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_all_future_notifications_from_user — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_all_future_notifications_from_user — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement get_future_notifications_from_user — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_future_notifications_from_user — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

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
        attachments: "list[AnyNotificationAttachment] | None" = None,
        tenant: str | None = None,
        lock: "asyncio.Lock | None" = None,
    ) -> "Notification":
        """TODO: implement persist_notification — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_notification — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

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
        attachments: "list[AnyNotificationAttachment] | None" = None,
        tenant: str | None = None,
        lock: "asyncio.Lock | None" = None,
    ) -> "OneOffNotification":
        """TODO: implement persist_one_off_notification — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_one_off_notification — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
        lock: "asyncio.Lock | None" = None,
    ) -> "Notification | OneOffNotification":
        """TODO: implement persist_notification_update — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement persist_notification_update — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: "asyncio.Lock | None" = None
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_pending_as_sent — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_pending_as_sent — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: "asyncio.Lock | None" = None
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_pending_as_failed — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_pending_as_failed — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: "asyncio.Lock | None" = None
    ) -> "Notification | OneOffNotification":
        """TODO: implement mark_sent_as_read — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_sent_as_read — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
        lock: "asyncio.Lock | None" = None,
    ) -> Iterable["Notification"]:
        """TODO: implement mark_sent_as_read_bulk — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement mark_sent_as_read_bulk — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: "asyncio.Lock | None" = None
    ) -> None:
        """TODO: implement cancel_notification — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement cancel_notification — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification | OneOffNotification":
        """TODO: implement get_notification — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_notification — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """TODO: implement filter_all_in_app_unread_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_all_in_app_unread_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]:
        """TODO: implement filter_in_app_unread_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_in_app_unread_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def filter_all_in_app_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """TODO: implement filter_all_in_app_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_all_in_app_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> Iterable["Notification"]:
        """TODO: implement filter_in_app_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_in_app_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def filter_notifications(
        self,
        filter: "NotificationFilter",  # noqa: A002
        page: int,
        page_size: int,
        order_by: "NotificationOrderBy | None" = None,
    ) -> Iterable["Notification | OneOffNotification"]:
        """TODO: implement filter_notifications — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement filter_notifications — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        """TODO: implement get_user_email_from_notification — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_user_email_from_notification — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
        lock: "asyncio.Lock | None" = None,
    ) -> None:
        """TODO: implement store_context_used — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_context_used — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def store_git_commit_sha(
        self,
        notification_id: int | str | uuid.UUID,
        git_commit_sha: str,
        lock: "asyncio.Lock | None" = None,
    ) -> None:
        """TODO: implement store_git_commit_sha — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_git_commit_sha — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def store_attachment_file_record(
        self, record: "AttachmentFileRecord", lock: "asyncio.Lock | None" = None
    ) -> "AttachmentFileRecord":
        """TODO: implement store_attachment_file_record — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement store_attachment_file_record — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_attachment_file_record(self, file_id: str) -> "AttachmentFileRecord | None":
        """TODO: implement get_attachment_file_record — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_attachment_file_record — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def find_attachment_file_by_checksum(
        self, checksum: str, size: int
    ) -> "AttachmentFileRecord | None":
        """TODO: implement find_attachment_file_by_checksum — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement find_attachment_file_by_checksum — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def delete_attachment_file(
        self, file_id: str, lock: "asyncio.Lock | None" = None
    ) -> None:
        """TODO: implement delete_attachment_file — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_attachment_file — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_orphaned_attachment_files(self) -> "Iterable[AttachmentFileRecord]":
        """TODO: implement get_orphaned_attachment_files — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_orphaned_attachment_files — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def get_attachments(
        self, notification_id: int | str | uuid.UUID
    ) -> "Iterable[StoredAttachment]":
        """TODO: implement get_attachments — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement get_attachments — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )

    async def delete_notification_attachment(
        self, attachment_id: int | str | uuid.UUID, lock: "asyncio.Lock | None" = None
    ) -> None:
        """TODO: implement delete_notification_attachment — see vintasend/services/notification_backends/asyncio_base.py for the contract."""
        raise NotImplementedError(
            "TODO: implement delete_notification_attachment — see "
            "vintasend/services/notification_backends/asyncio_base.py for the contract"
        )
