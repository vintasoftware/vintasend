import asyncio
import datetime
import io
import json
import os
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import BinaryIO, cast

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import AttachmentFileNotFoundError, NotificationNotFoundError
from vintasend.services.attachment_managers.stubs.fake_attachment_manager import (
    FakeAsyncIOAttachmentManager,
    FakeAttachmentManager,
)
from vintasend.services.dataclasses import (
    AnyNotificationAttachment,
    AttachmentFile,
    AttachmentFileRecord,
    Notification,
    NotificationAttachment,
    OneOffNotification,
    StoredAttachment,
    UpdateNotificationKwargs,
    is_attachment_reference,
)
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_backends.filters import (
    NotificationFilter,
    NotificationOrderBy,
    matches_filter,
    sort_notifications,
)


class FakeFileAttachmentFile(AttachmentFile):
    """In-memory attachment file for testing"""

    def __init__(self, file_data: bytes, filename: str):
        self.file_data = file_data
        self.filename = filename
        self._deleted = False

    def read(self) -> bytes:
        if self._deleted:
            raise FileNotFoundError("Attachment file has been deleted")
        return self.file_data

    def stream(self) -> BinaryIO:
        if self._deleted:
            raise FileNotFoundError("Attachment file has been deleted")
        return io.BytesIO(self.file_data)

    def url(self, expires_in: int = 3600) -> str:
        # For testing, return a fake URL
        return f"fake://attachment/{self.filename}?expires_in={expires_in}"

    def delete(self) -> None:
        # For in-memory storage, just mark as deleted
        self._deleted = True
        self.file_data = b""


class FakeFileBackend(BaseNotificationBackend):
    notifications: list[Notification | OneOffNotification]
    database_file_name: str

    def __init__(self, database_file_name: str = "notifications.json", **kwargs):
        super().__init__(database_file_name=database_file_name, **kwargs)
        self.database_file_name = database_file_name
        # In-memory file records (keyed by id) and notification->file join rows. The bytes
        # they describe live in the attachment manager, not here.
        self._attachment_file_records: dict[str, AttachmentFileRecord] = {}
        self._attachment_join_rows: list[dict] = []
        # Default to the in-memory fake manager so the backend is usable standalone; the
        # service replaces this via inject_attachment_manager when one is configured.
        self._attachment_manager = FakeAttachmentManager()
        try:
            notifications_file = open(self.database_file_name, encoding="utf-8")
        except FileNotFoundError:
            self.notifications = []
            return
        try:
            self.notifications = [
                self._convert_json_to_notification(n) for n in json.load(notifications_file)
            ]
            notifications_file.close()
        except json.JSONDecodeError:
            self.notifications = []
            return

    def clear(self):
        self.notifications = []
        self._attachment_file_records = {}
        self._attachment_join_rows = []
        try:
            os.remove(self.database_file_name)
        except FileNotFoundError:
            pass

    def get_future_notifications(
        self, page: int, page_size: int
    ) -> list[Notification | OneOffNotification]:
        return cast(
            list[Notification | OneOffNotification],
            self.__paginate_notifications(self.get_all_future_notifications(), page, page_size),
        )

    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification | OneOffNotification]:
        return cast(
            list[Notification | OneOffNotification],
            self.__paginate_notifications(
                self.get_all_future_notifications_from_user(user_id), page, page_size
            ),
        )

    def get_all_future_notifications(self) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is not None
                and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc)
            )
        ]

    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is not None
                and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc)
            )
            and (
                (isinstance(n, Notification) and str(n.user_id) == str(user_id))
                or isinstance(n, OneOffNotification)  # OneOffNotifications don't have user_id
            )
        ]

    def get_all_pending_notifications(self) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is None
                or n.send_after <= datetime.datetime.now(tz=datetime.timezone.utc)
            )
        ]

    def _convert_notification_to_json(
        self, notification: Notification | OneOffNotification
    ) -> dict:
        if isinstance(notification, OneOffNotification):
            return {
                "id": str(notification.id),
                "email_or_phone": notification.email_or_phone,
                "first_name": notification.first_name,
                "last_name": notification.last_name,
                "notification_type": notification.notification_type,
                "title": notification.title,
                "body_template": notification.body_template,
                "context_name": notification.context_name,
                "context_kwargs": notification.context_kwargs,
                "send_after": notification.send_after.isoformat()
                if notification.send_after
                else None,
                "subject_template": notification.subject_template,
                "preheader_template": notification.preheader_template,
                "status": notification.status,
                "context_used": notification.context_used,
                "adapter_extra_parameters": notification.adapter_extra_parameters,
                "is_one_off": True,
                "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
                "read_at": notification.read_at.isoformat() if notification.read_at else None,
                "tenant": notification.tenant,
            }
        else:
            return {
                "id": str(notification.id),
                "user_id": str(notification.user_id),
                "notification_type": notification.notification_type,
                "title": notification.title,
                "body_template": notification.body_template,
                "context_name": notification.context_name,
                "context_kwargs": notification.context_kwargs,
                "send_after": notification.send_after.isoformat()
                if notification.send_after
                else None,
                "subject_template": notification.subject_template,
                "preheader_template": notification.preheader_template,
                "status": notification.status,
                "context_used": notification.context_used,
                "adapter_extra_parameters": notification.adapter_extra_parameters,
                "is_one_off": False,
                "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
                "read_at": notification.read_at.isoformat() if notification.read_at else None,
                "tenant": notification.tenant,
            }

    def _convert_json_to_notification(
        self, notification: dict
    ) -> Notification | OneOffNotification:
        # Determine if this is a OneOffNotification based on presence of email_or_phone field
        if "email_or_phone" in notification:
            return OneOffNotification(
                id=notification["id"],
                email_or_phone=notification["email_or_phone"],
                first_name=notification["first_name"],
                last_name=notification["last_name"],
                notification_type=notification["notification_type"],
                title=notification["title"],
                body_template=notification["body_template"],
                context_name=notification["context_name"],
                context_kwargs=notification["context_kwargs"],
                send_after=(
                    datetime.datetime.fromisoformat(notification["send_after"])
                    if notification["send_after"]
                    else None
                ),
                subject_template=notification["subject_template"],
                preheader_template=notification["preheader_template"],
                status=notification["status"],
                context_used=notification.get("context_used"),
                adapter_extra_parameters=notification.get("adapter_extra_parameters"),
                sent_at=(
                    datetime.datetime.fromisoformat(notification["sent_at"])
                    if notification.get("sent_at")
                    else None
                ),
                read_at=(
                    datetime.datetime.fromisoformat(notification["read_at"])
                    if notification.get("read_at")
                    else None
                ),
                tenant=notification.get("tenant"),
            )
        else:
            return Notification(
                id=notification["id"],
                user_id=notification["user_id"],
                notification_type=notification["notification_type"],
                title=notification["title"],
                body_template=notification["body_template"],
                context_name=notification["context_name"],
                context_kwargs=notification["context_kwargs"],
                send_after=(
                    datetime.datetime.fromisoformat(notification["send_after"])
                    if notification["send_after"]
                    else None
                ),
                subject_template=notification["subject_template"],
                preheader_template=notification["preheader_template"],
                status=notification["status"],
                context_used=notification.get("context_used"),
                adapter_extra_parameters=notification.get("adapter_extra_parameters"),
                sent_at=(
                    datetime.datetime.fromisoformat(notification["sent_at"])
                    if notification.get("sent_at")
                    else None
                ),
                read_at=(
                    datetime.datetime.fromisoformat(notification["read_at"])
                    if notification.get("read_at")
                    else None
                ),
                tenant=notification.get("tenant"),
            )

    def _store_notifications(self):
        json_output_file = open(self.database_file_name, "w", encoding="utf-8")
        json.dump(
            [self._convert_notification_to_json(n) for n in self.notifications],
            json_output_file,
        )
        json_output_file.close()

    def get_pending_notifications(
        self, page: int, page_size: int
    ) -> list[Notification | OneOffNotification]:
        # page is 1-indexed
        return self.get_all_pending_notifications()[
            ((page - 1) * page_size) : ((page - 1) * page_size) + page_size
        ]

    def persist_notification(
        self,
        user_id: uuid.UUID | str | int,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> Notification:
        notification_id = str(uuid.uuid4())
        stored_attachments = self._store_attachments(attachments or [], notification_id)

        notification = Notification(
            id=notification_id,
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            status=NotificationStatus.PENDING_SEND.value,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=stored_attachments,
            tenant=tenant,
        )
        self.notifications.append(notification)
        self._store_notifications()
        return notification

    def _store_attachments(
        self,
        attachments: list[AnyNotificationAttachment],
        notification_id: int | str | uuid.UUID,
    ) -> list[StoredAttachment]:
        """Persist attachments by delegating every byte operation to the injected manager.

        The backend never reads a file itself. For an upload, the checksum + size is
        looked up against existing file records first: on a hit the existing
        `AttachmentFileRecord` is reused and the manager upload is skipped entirely; on a
        miss the manager stores the bytes and the resulting record is persisted. For a
        reference, `file_id` is resolved against the existing records -- raising
        `AttachmentFileNotFoundError` if absent -- and only a join row is written; there is
        no upload and no new record. Either way, the returned handle is rebuilt through the
        manager.
        """
        manager = self._attachment_manager
        assert manager is not None  # noqa: S101 - the fake always has a default manager
        stored_attachments = []

        for attachment in attachments:
            if is_attachment_reference(attachment):
                record = self.get_attachment_file_record(attachment.file_id)
                if record is None:
                    raise AttachmentFileNotFoundError(
                        f"No attachment file record found for file_id={attachment.file_id!r}"
                    )
                join_id = str(uuid.uuid4())
                self._attachment_join_rows.append(
                    {
                        "id": join_id,
                        "notification_id": notification_id,
                        "file_id": record.id,
                        "description": attachment.description,
                        "is_inline": attachment.is_inline,
                    }
                )
                attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
                stored_attachments.append(
                    StoredAttachment(
                        id=join_id,
                        filename=record.filename,
                        content_type=record.content_type,
                        size=record.size,
                        checksum=record.checksum,
                        created_at=record.created_at,
                        file=attachment_file,
                        description=attachment.description,
                        is_inline=attachment.is_inline,
                        file_id=record.id,
                        storage_identifiers=record.storage_identifiers,
                    )
                )
                continue

            # TypeGuard narrows only the positive branch, so restate the upload type.
            assert isinstance(attachment, NotificationAttachment)  # noqa: S101

            # Read the bytes once, up front, so the checksum lookup and (on a miss) the
            # upload itself never re-read the same path/URL/stream twice.
            data = manager.file_to_bytes(attachment.file)
            checksum = manager.calculate_checksum(data)
            existing_record = self.find_attachment_file_by_checksum(checksum, len(data))
            if existing_record is not None:
                record = existing_record
            else:
                record = manager.upload_file(data, attachment.filename, attachment.content_type)
                self.store_attachment_file_record(record)

            join_id = str(uuid.uuid4())
            self._attachment_join_rows.append(
                {
                    "id": join_id,
                    "notification_id": notification_id,
                    "file_id": record.id,
                    "description": attachment.description,
                    "is_inline": attachment.is_inline,
                }
            )

            attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
            stored_attachments.append(
                StoredAttachment(
                    id=join_id,
                    filename=record.filename,
                    content_type=record.content_type,
                    size=record.size,
                    checksum=record.checksum,
                    created_at=record.created_at,
                    file=attachment_file,
                    description=attachment.description,
                    is_inline=attachment.is_inline,
                    file_id=record.id,
                    storage_identifiers=record.storage_identifiers,
                )
            )

        return stored_attachments

    def store_attachment_file_record(self, record: AttachmentFileRecord) -> AttachmentFileRecord:
        self._attachment_file_records[record.id] = record
        return record

    def get_attachment_file_record(self, file_id: str) -> AttachmentFileRecord | None:
        return self._attachment_file_records.get(file_id)

    def find_attachment_file_by_checksum(
        self, checksum: str, size: int
    ) -> AttachmentFileRecord | None:
        for record in self._attachment_file_records.values():
            if record.checksum == checksum and record.size == size:
                return record
        return None

    def delete_attachment_file(self, file_id: str) -> None:
        self._attachment_file_records.pop(file_id, None)

    def get_orphaned_attachment_files(self) -> list[AttachmentFileRecord]:
        """Return file records no longer referenced by any notification join row.

        Reclaiming one is a caller-driven, two-step operation this method only surfaces
        candidates for: `manager.delete_file_by_identifiers(record.storage_identifiers)`
        to remove the bytes, then `backend.delete_attachment_file(record.id)` to drop the
        row. Nothing here deletes anything automatically -- see the docstrings on
        `cancel_notification` and `delete_notification_attachment` for why.
        """
        referenced = {row["file_id"] for row in self._attachment_join_rows}
        return [
            record
            for file_id, record in self._attachment_file_records.items()
            if file_id not in referenced
        ]

    def get_attachments(self, notification_id: int | str | uuid.UUID) -> list[StoredAttachment]:
        manager = self._attachment_manager
        assert manager is not None  # noqa: S101 - the fake always has a default manager
        stored_attachments = []
        for row in self._attachment_join_rows:
            if str(row["notification_id"]) != str(notification_id):
                continue
            record = self._attachment_file_records.get(row["file_id"])
            if record is None:
                continue
            attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
            stored_attachments.append(
                StoredAttachment(
                    id=row["id"],
                    filename=record.filename,
                    content_type=record.content_type,
                    size=record.size,
                    checksum=record.checksum,
                    created_at=record.created_at,
                    file=attachment_file,
                    description=row["description"],
                    is_inline=row["is_inline"],
                    file_id=record.id,
                    storage_identifiers=record.storage_identifiers,
                )
            )
        return stored_attachments

    def delete_notification_attachment(self, attachment_id: int | str | uuid.UUID) -> None:
        """Delete a single notification attachment join row by its own id.

        This drops only the join row, never the underlying `AttachmentFileRecord` or its
        bytes -- a file may still back other notifications. Reclaiming an orphaned file is
        a separate, caller-driven step via `get_orphaned_attachment_files`.
        """
        self._attachment_join_rows = [
            row for row in self._attachment_join_rows if str(row["id"]) != str(attachment_id)
        ]

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
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> OneOffNotification:
        notification_id = uuid.uuid4()
        stored_attachments = self._store_attachments(attachments or [], notification_id)

        notification = OneOffNotification(
            id=notification_id,
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
            status=NotificationStatus.PENDING_SEND.value,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=stored_attachments,
            tenant=tenant,
        )
        self.notifications.append(notification)
        self._store_notifications()
        return notification

    def persist_notification_update(
        self, notification_id: int | str | uuid.UUID, update_data: UpdateNotificationKwargs
    ) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)

        for key, value in update_data.items():
            setattr(notification, key, value)

        self._store_notifications()
        return notification

    def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.SENT.value
        notification.sent_at = datetime.datetime.now(tz=datetime.timezone.utc)
        self._store_notifications()
        return notification

    def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.FAILED.value
        self._store_notifications()
        return notification

    def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.READ.value
        notification.read_at = datetime.datetime.now(tz=datetime.timezone.utc)
        self._store_notifications()
        return notification

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """Cancel a notification.

        Deliberately no attachment hook: cancelling a notification does not delete its
        attachment files or their join rows. Cascade, if any, is the schema's job (e.g. a
        `CASCADE` foreign key in a real backend); reclaiming files that end up unreferenced
        is `get_orphaned_attachment_files` plus the two-step deletion, not something this
        method does implicitly.
        """
        notification = self.get_notification(notification_id)
        self.notifications.remove(notification)
        self._store_notifications()

    def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> Notification | OneOffNotification:
        try:
            return next(n for n in self.notifications if str(n.id) == str(notification_id))
        except StopIteration as e:
            raise NotificationNotFoundError("Notification not found") from e

    def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification]:
        from vintasend.services.dataclasses import OneOffNotification

        notifications = [
            n
            for n in self.notifications
            if not isinstance(n, OneOffNotification)
            and n.user_id == user_id
            and n.status == NotificationStatus.SENT.value
            and n.notification_type == NotificationTypes.IN_APP.value
        ]
        return notifications

    def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification]:
        return cast(
            list[Notification],
            self.__paginate_notifications(
                self.filter_all_in_app_unread_notifications(user_id), page, page_size
            ),
        )

    def filter_all_in_app_notifications(self, user_id: int | str | uuid.UUID) -> list[Notification]:
        notifications = [
            n
            for n in self.notifications
            if not isinstance(n, OneOffNotification)
            and n.user_id == user_id
            and n.status in (NotificationStatus.SENT.value, NotificationStatus.READ.value)
            and n.notification_type == NotificationTypes.IN_APP.value
        ]
        return cast(list[Notification], notifications)

    def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> list[Notification]:
        return cast(
            list[Notification],
            self.__paginate_notifications(
                self.filter_all_in_app_notifications(user_id), page, page_size
            ),
        )

    def count_in_app_notifications(self, user_id: int | str | uuid.UUID) -> int:
        return len(self.filter_all_in_app_notifications(user_id))

    def count_in_app_unread_notifications(self, user_id: int | str | uuid.UUID) -> int:
        return len(self.filter_all_in_app_unread_notifications(user_id))

    def filter_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        page: int,
        page_size: int,
        order_by: NotificationOrderBy | None = None,
    ) -> list[Notification | OneOffNotification]:
        # Reference implementation: match every notification against the recursive predicate,
        # order it stably (id tiebreaker in the primary direction), then paginate. Downstream
        # backends translate ``matches_filter`` / ``sort_notifications`` into their own query
        # language; the semantics here are the contract they mirror.
        matched = [n for n in self.notifications if matches_filter(n, filter)]
        ordered = sort_notifications(matched, order_by)
        return cast(
            list[Notification | OneOffNotification],
            self.__paginate_notifications(ordered, page, page_size),
        )

    def count_notifications(self, filter: NotificationFilter) -> int:  # noqa: A002
        return sum(1 for n in self.notifications if matches_filter(n, filter))

    def get_filter_capabilities(self) -> dict[str, bool]:
        # This fake supports the full vocabulary, so it declines nothing: an empty report means
        # every capability is supported once merged over the all-``True`` default.
        return {}

    def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> list[Notification]:
        ids = {str(i) for i in notification_ids}
        result: list[Notification] = []
        changed = False
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        for n in self.notifications:
            if str(n.id) not in ids or isinstance(n, OneOffNotification):
                continue
            if user_id is not None and str(n.user_id) != str(user_id):
                continue
            if n.status == NotificationStatus.SENT.value:
                n.status = NotificationStatus.READ.value
                n.read_at = now
                changed = True
            if n.status == NotificationStatus.READ.value:
                result.append(n)
        if changed:
            self._store_notifications()
        return result

    def __paginate_notifications(
        self,
        notifications: list[Notification | OneOffNotification] | list[Notification],
        page: int,
        page_size: int,
    ) -> list[Notification | OneOffNotification] | list[Notification]:
        # page is 1-indexed
        return notifications[((page - 1) * page_size) : ((page - 1) * page_size) + page_size]

    def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        notification = self.get_notification(notification_id)
        return str(notification.context_kwargs.get("email", "testemail@example.com"))

    def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
    ) -> None:
        notification = self.get_notification(notification_id)
        notification.context_used = context
        notification.adapter_used = adapter_import_str
        self._store_notifications()


class Config:
    def __init__(self, config_a: Decimal | None = None, config_b: datetime.datetime | None = None):
        self.config_a = config_a if config_a is not None else Decimal("1.0")
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        self.config_b = config_b if config_b is not None else now


class FakeFileBackendWithNonSerializableKWArgs(FakeFileBackend):
    config: Config

    def __init__(
        self, database_file_name: str = "notifications.json", config: Config | None = None
    ):
        super().__init__(database_file_name=database_file_name, config=config)

    def _store_notifications(self):
        assert self.config.config_a == Decimal("1.0")
        assert isinstance(self.config.config_b, datetime.datetime)
        super()._store_notifications()


class InvalidBackend:
    def __init__(self, *_args, **_kwargs):
        pass


class FakeAsyncIOFileBackend(AsyncIOBaseNotificationBackend):
    notifications: list[Notification | OneOffNotification]
    database_file_name: str

    def __init__(self, database_file_name: str = "notifications.json", **kwargs):
        super().__init__(database_file_name=database_file_name, **kwargs)
        self.database_file_name = database_file_name
        # In-memory file records (keyed by id) and notification->file join rows. The bytes
        # they describe live in the attachment manager, not here.
        self._attachment_file_records: dict[str, AttachmentFileRecord] = {}
        self._attachment_join_rows: list[dict] = []
        # Default to the in-memory fake manager so the backend is usable standalone; the
        # service replaces this via inject_attachment_manager when one is configured.
        self._attachment_manager = FakeAsyncIOAttachmentManager()
        try:
            notifications_file = open(self.database_file_name, encoding="utf-8")
        except FileNotFoundError:
            self.notifications = []
            return
        try:
            self.notifications = [
                self._convert_json_to_notification(n) for n in json.load(notifications_file)
            ]
            notifications_file.close()
        except json.JSONDecodeError:
            self.notifications = []
            return

    async def clear(self):
        self.notifications = []
        self._attachment_file_records = {}
        self._attachment_join_rows = []
        try:
            os.remove(self.database_file_name)
        except FileNotFoundError:
            pass

    async def _store_attachments(
        self,
        attachments: list[AnyNotificationAttachment],
        notification_id: int | str | uuid.UUID,
        lock: asyncio.Lock | None = None,
    ) -> list[StoredAttachment]:
        """Persist attachments by delegating every byte operation to the injected manager.

        The backend never reads a file itself. For an upload, the checksum + size is
        looked up against existing file records first: on a hit the existing
        `AttachmentFileRecord` is reused and the (async) manager upload is skipped
        entirely; on a miss the manager stores the bytes and the resulting record is
        persisted. For a reference, `file_id` is resolved against the existing records --
        raising `AttachmentFileNotFoundError` if absent -- and only a join row is written;
        there is no upload and no new record. Either way, the returned handle is rebuilt
        through the manager.
        """
        manager = self._attachment_manager
        assert manager is not None  # noqa: S101 - the fake always has a default manager
        stored_attachments = []

        for attachment in attachments:
            if is_attachment_reference(attachment):
                record = await self.get_attachment_file_record(attachment.file_id)
                if record is None:
                    raise AttachmentFileNotFoundError(
                        f"No attachment file record found for file_id={attachment.file_id!r}"
                    )
                join_id = str(uuid.uuid4())
                self._attachment_join_rows.append(
                    {
                        "id": join_id,
                        "notification_id": notification_id,
                        "file_id": record.id,
                        "description": attachment.description,
                        "is_inline": attachment.is_inline,
                    }
                )
                attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
                stored_attachments.append(
                    StoredAttachment(
                        id=join_id,
                        filename=record.filename,
                        content_type=record.content_type,
                        size=record.size,
                        checksum=record.checksum,
                        created_at=record.created_at,
                        file=attachment_file,
                        description=attachment.description,
                        is_inline=attachment.is_inline,
                        file_id=record.id,
                        storage_identifiers=record.storage_identifiers,
                    )
                )
                continue

            # TypeGuard narrows only the positive branch, so restate the upload type.
            assert isinstance(attachment, NotificationAttachment)  # noqa: S101

            # Read the bytes once, up front -- file_to_bytes / calculate_checksum are
            # sync on the manager even here, so the checksum lookup and (on a miss) the
            # upload itself never re-read the same path/URL/stream twice.
            data = manager.file_to_bytes(attachment.file)
            checksum = manager.calculate_checksum(data)
            existing_record = await self.find_attachment_file_by_checksum(checksum, len(data))
            if existing_record is not None:
                record = existing_record
            else:
                record = await manager.upload_file(
                    data, attachment.filename, attachment.content_type
                )
                await self.store_attachment_file_record(record, lock)

            join_id = str(uuid.uuid4())
            self._attachment_join_rows.append(
                {
                    "id": join_id,
                    "notification_id": notification_id,
                    "file_id": record.id,
                    "description": attachment.description,
                    "is_inline": attachment.is_inline,
                }
            )

            attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
            stored_attachments.append(
                StoredAttachment(
                    id=join_id,
                    filename=record.filename,
                    content_type=record.content_type,
                    size=record.size,
                    checksum=record.checksum,
                    created_at=record.created_at,
                    file=attachment_file,
                    description=attachment.description,
                    is_inline=attachment.is_inline,
                    file_id=record.id,
                    storage_identifiers=record.storage_identifiers,
                )
            )

        return stored_attachments

    async def store_attachment_file_record(
        self, record: AttachmentFileRecord, lock: asyncio.Lock | None = None
    ) -> AttachmentFileRecord:
        self._attachment_file_records[record.id] = record
        return record

    async def get_attachment_file_record(self, file_id: str) -> AttachmentFileRecord | None:
        return self._attachment_file_records.get(file_id)

    async def find_attachment_file_by_checksum(
        self, checksum: str, size: int
    ) -> AttachmentFileRecord | None:
        for record in self._attachment_file_records.values():
            if record.checksum == checksum and record.size == size:
                return record
        return None

    async def delete_attachment_file(self, file_id: str, lock: asyncio.Lock | None = None) -> None:
        self._attachment_file_records.pop(file_id, None)

    async def get_orphaned_attachment_files(self) -> list[AttachmentFileRecord]:
        """Return file records no longer referenced by any notification join row.

        Reclaiming one is a caller-driven, two-step operation this method only surfaces
        candidates for: `manager.delete_file_by_identifiers(record.storage_identifiers)`
        to remove the bytes, then `backend.delete_attachment_file(record.id)` to drop the
        row. Nothing here deletes anything automatically -- see the docstrings on
        `cancel_notification` and `delete_notification_attachment` for why.
        """
        referenced = {row["file_id"] for row in self._attachment_join_rows}
        return [
            record
            for file_id, record in self._attachment_file_records.items()
            if file_id not in referenced
        ]

    async def get_attachments(
        self, notification_id: int | str | uuid.UUID
    ) -> list[StoredAttachment]:
        manager = self._attachment_manager
        assert manager is not None  # noqa: S101 - the fake always has a default manager
        stored_attachments = []
        for row in self._attachment_join_rows:
            if str(row["notification_id"]) != str(notification_id):
                continue
            record = self._attachment_file_records.get(row["file_id"])
            if record is None:
                continue
            attachment_file = manager.reconstruct_attachment_file(record.storage_identifiers)
            stored_attachments.append(
                StoredAttachment(
                    id=row["id"],
                    filename=record.filename,
                    content_type=record.content_type,
                    size=record.size,
                    checksum=record.checksum,
                    created_at=record.created_at,
                    file=attachment_file,
                    description=row["description"],
                    is_inline=row["is_inline"],
                    file_id=record.id,
                    storage_identifiers=record.storage_identifiers,
                )
            )
        return stored_attachments

    async def delete_notification_attachment(
        self, attachment_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
        """Delete a single notification attachment join row by its own id.

        This drops only the join row, never the underlying `AttachmentFileRecord` or its
        bytes -- a file may still back other notifications. Reclaiming an orphaned file is
        a separate, caller-driven step via `get_orphaned_attachment_files`.
        """
        self._attachment_join_rows = [
            row for row in self._attachment_join_rows if str(row["id"]) != str(attachment_id)
        ]

    async def get_future_notifications(self, page: int, page_size: int) -> list[Notification]:
        return cast(
            list[Notification],
            self.__paginate_notifications(
                await self.get_all_future_notifications(), page, page_size
            ),
        )

    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification]:
        return cast(
            list[Notification],
            self.__paginate_notifications(
                await self.get_all_future_notifications_from_user(user_id), page, page_size
            ),
        )

    async def get_all_future_notifications(self) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is not None
                and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc)
            )
        ]

    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if isinstance(n, Notification)
            and n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is not None
                and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc)
            )
            and str(n.user_id) == str(user_id)
        ]

    async def get_all_pending_notifications(self) -> list[Notification | OneOffNotification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (
                n.send_after is None
                or n.send_after <= datetime.datetime.now(tz=datetime.timezone.utc)
            )
        ]

    def _convert_notification_to_json(
        self, notification: Notification | OneOffNotification
    ) -> dict:
        base_dict = {
            "id": str(notification.id),
            "notification_type": notification.notification_type,
            "title": notification.title,
            "body_template": notification.body_template,
            "context_name": notification.context_name,
            "context_kwargs": notification.context_kwargs,
            "send_after": notification.send_after.isoformat() if notification.send_after else None,
            "subject_template": notification.subject_template,
            "preheader_template": notification.preheader_template,
            "status": notification.status,
            "context_used": notification.context_used,
            "sent_at": notification.sent_at.isoformat() if notification.sent_at else None,
            "read_at": notification.read_at.isoformat() if notification.read_at else None,
            "tenant": notification.tenant,
        }

        if isinstance(notification, OneOffNotification):
            base_dict.update(
                {
                    "email_or_phone": notification.email_or_phone,
                    "first_name": notification.first_name,
                    "last_name": notification.last_name,
                }
            )
        else:
            base_dict["user_id"] = str(notification.user_id)

        return base_dict

    def _convert_json_to_notification(
        self, notification: dict
    ) -> Notification | OneOffNotification:
        # Determine if this is a OneOffNotification based on presence of email_or_phone field
        if "email_or_phone" in notification:
            return OneOffNotification(
                id=notification["id"],
                email_or_phone=notification["email_or_phone"],
                first_name=notification["first_name"],
                last_name=notification["last_name"],
                notification_type=notification["notification_type"],
                title=notification["title"],
                body_template=notification["body_template"],
                context_name=notification["context_name"],
                context_kwargs=notification["context_kwargs"],
                send_after=(
                    datetime.datetime.fromisoformat(notification["send_after"])
                    if notification["send_after"]
                    else None
                ),
                subject_template=notification["subject_template"],
                preheader_template=notification["preheader_template"],
                status=notification["status"],
                context_used=notification.get("context_used"),
                adapter_extra_parameters=notification.get("adapter_extra_parameters"),
                sent_at=(
                    datetime.datetime.fromisoformat(notification["sent_at"])
                    if notification.get("sent_at")
                    else None
                ),
                read_at=(
                    datetime.datetime.fromisoformat(notification["read_at"])
                    if notification.get("read_at")
                    else None
                ),
                tenant=notification.get("tenant"),
            )
        else:
            return Notification(
                id=notification["id"],
                user_id=notification["user_id"],
                notification_type=notification["notification_type"],
                title=notification["title"],
                body_template=notification["body_template"],
                context_name=notification["context_name"],
                context_kwargs=notification["context_kwargs"],
                send_after=(
                    datetime.datetime.fromisoformat(notification["send_after"])
                    if notification["send_after"]
                    else None
                ),
                subject_template=notification["subject_template"],
                preheader_template=notification["preheader_template"],
                status=notification["status"],
                context_used=notification.get("context_used"),
                adapter_extra_parameters=notification.get("adapter_extra_parameters"),
                sent_at=(
                    datetime.datetime.fromisoformat(notification["sent_at"])
                    if notification.get("sent_at")
                    else None
                ),
                read_at=(
                    datetime.datetime.fromisoformat(notification["read_at"])
                    if notification.get("read_at")
                    else None
                ),
                tenant=notification.get("tenant"),
            )

    async def _store_notifications(self, lock: asyncio.Lock | None = None):
        if lock is not None:
            await lock.acquire()
        json_output_file = open(self.database_file_name, "w", encoding="utf-8")
        json.dump(
            [self._convert_notification_to_json(n) for n in self.notifications],
            json_output_file,
        )
        json_output_file.close()
        if lock is not None:
            lock.release()

    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> list[Notification | OneOffNotification]:
        pending_notifications = await self.get_all_pending_notifications()
        return pending_notifications[
            ((page - 1) * page_size) : ((page - 1) * page_size) + page_size
        ]

    async def persist_notification(
        self,
        user_id: uuid.UUID | str | int,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
        lock: asyncio.Lock | None = None,
    ) -> Notification:
        notification_id = str(uuid.uuid4())
        stored_attachments = await self._store_attachments(attachments or [], notification_id, lock)

        notification = Notification(
            id=notification_id,
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body_template=body_template,
            context_name=context_name,
            context_kwargs=context_kwargs,
            send_after=send_after,
            subject_template=subject_template,
            preheader_template=preheader_template,
            status=NotificationStatus.PENDING_SEND.value,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=stored_attachments,
            tenant=tenant,
        )
        self.notifications.append(notification)
        await self._store_notifications(lock)
        return notification

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
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
        lock: asyncio.Lock | None = None,
    ) -> OneOffNotification:
        notification_id = uuid.uuid4()
        stored_attachments = await self._store_attachments(attachments or [], notification_id, lock)

        notification = OneOffNotification(
            id=notification_id,
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
            status=NotificationStatus.PENDING_SEND.value,
            adapter_extra_parameters=adapter_extra_parameters,
            attachments=stored_attachments,
            tenant=tenant,
        )
        self.notifications.append(notification)
        await self._store_notifications(lock)
        return notification

    async def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: UpdateNotificationKwargs,
        lock: asyncio.Lock | None = None,
    ) -> Notification | OneOffNotification:
        notification = await self.get_notification(notification_id)

        for key, value in update_data.items():
            setattr(notification, key, value)

        await self._store_notifications(lock)
        return notification

    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> Notification | OneOffNotification:
        notification = await self.get_notification(notification_id)
        notification.status = NotificationStatus.SENT.value
        notification.sent_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await self._store_notifications(lock)
        return notification

    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> Notification | OneOffNotification:
        notification = await self.get_notification(notification_id)
        notification.status = NotificationStatus.FAILED.value
        await self._store_notifications(lock)
        return notification

    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> Notification | OneOffNotification:
        notification = await self.get_notification(notification_id)
        notification.status = NotificationStatus.READ.value
        notification.read_at = datetime.datetime.now(tz=datetime.timezone.utc)
        await self._store_notifications(lock)
        return notification

    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
        """Cancel a notification.

        Deliberately no attachment hook: cancelling a notification does not delete its
        attachment files or their join rows. Cascade, if any, is the schema's job (e.g. a
        `CASCADE` foreign key in a real backend); reclaiming files that end up unreferenced
        is `get_orphaned_attachment_files` plus the two-step deletion, not something this
        method does implicitly.
        """
        notification = await self.get_notification(notification_id)
        self.notifications.remove(notification)
        await self._store_notifications(lock)

    async def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> Notification | OneOffNotification:
        try:
            return next(n for n in self.notifications if str(n.id) == str(notification_id))
        except StopIteration as e:
            raise NotificationNotFoundError("Notification not found") from e

    async def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification]:
        notifications = [
            n
            for n in self.notifications
            if not isinstance(n, OneOffNotification)
            and n.user_id == user_id
            and n.status == NotificationStatus.SENT.value
            and n.notification_type == NotificationTypes.IN_APP.value
        ]
        return notifications

    async def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification]:
        in_app_unread_notifications = await self.filter_all_in_app_unread_notifications(user_id)
        return cast(
            list[Notification],
            self.__paginate_notifications(in_app_unread_notifications, page, page_size),
        )

    async def filter_all_in_app_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification]:
        notifications = [
            n
            for n in self.notifications
            if not isinstance(n, OneOffNotification)
            and n.user_id == user_id
            and n.status in (NotificationStatus.SENT.value, NotificationStatus.READ.value)
            and n.notification_type == NotificationTypes.IN_APP.value
        ]
        return cast(list[Notification], notifications)

    async def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> list[Notification]:
        in_app_notifications = await self.filter_all_in_app_notifications(user_id)
        return cast(
            list[Notification], self.__paginate_notifications(in_app_notifications, page, page_size)
        )

    async def count_in_app_notifications(self, user_id: int | str | uuid.UUID) -> int:
        return len(await self.filter_all_in_app_notifications(user_id))

    async def count_in_app_unread_notifications(self, user_id: int | str | uuid.UUID) -> int:
        return len(await self.filter_all_in_app_unread_notifications(user_id))

    async def filter_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        page: int,
        page_size: int,
        order_by: NotificationOrderBy | None = None,
    ) -> list[Notification | OneOffNotification]:
        # Reference implementation: match every notification against the recursive predicate,
        # order it stably (id tiebreaker in the primary direction), then paginate. Downstream
        # backends translate ``matches_filter`` / ``sort_notifications`` into their own query
        # language; the semantics here are the contract they mirror.
        matched = [n for n in self.notifications if matches_filter(n, filter)]
        ordered = sort_notifications(matched, order_by)
        return cast(
            list[Notification | OneOffNotification],
            self.__paginate_notifications(ordered, page, page_size),
        )

    async def count_notifications(self, filter: NotificationFilter) -> int:  # noqa: A002
        return sum(1 for n in self.notifications if matches_filter(n, filter))

    async def get_filter_capabilities(self) -> dict[str, bool]:
        # This fake supports the full vocabulary, so it declines nothing: an empty report means
        # every capability is supported once merged over the all-``True`` default.
        return {}

    async def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
        lock: asyncio.Lock | None = None,
    ) -> list[Notification]:
        ids = {str(i) for i in notification_ids}
        result: list[Notification] = []
        changed = False
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        for n in self.notifications:
            if str(n.id) not in ids or isinstance(n, OneOffNotification):
                continue
            if user_id is not None and str(n.user_id) != str(user_id):
                continue
            if n.status == NotificationStatus.SENT.value:
                n.status = NotificationStatus.READ.value
                n.read_at = now
                changed = True
            if n.status == NotificationStatus.READ.value:
                result.append(n)
        if changed:
            await self._store_notifications(lock)
        return result

    def __paginate_notifications(
        self,
        notifications: list[Notification | OneOffNotification] | list[Notification],
        page: int,
        page_size: int,
    ) -> list[Notification | OneOffNotification] | list[Notification]:
        return notifications[((page - 1) * page_size) : ((page - 1) * page_size) + page_size]

    async def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        notification = await self.get_notification(notification_id)
        return str(notification.context_kwargs.get("email", "testemail@example.com"))

    async def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
        lock: asyncio.Lock | None = None,
    ) -> None:
        notification = await self.get_notification(notification_id)
        notification.context_used = context
        notification.adapter_used = adapter_import_str
        await self._store_notifications(lock)
