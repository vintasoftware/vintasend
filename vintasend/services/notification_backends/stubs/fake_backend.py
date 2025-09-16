import asyncio
import datetime
import hashlib
import io
import json
import os
import uuid
from decimal import Decimal
from typing import BinaryIO, cast

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationNotFoundError
from vintasend.services.dataclasses import (
    AttachmentFile,
    Notification,
    NotificationAttachment,
    OneOffNotification,
    StoredAttachment,
    UpdateNotificationKwargs,
)
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend


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
        try:
            os.remove(self.database_file_name)
        except FileNotFoundError:
            pass

    def get_future_notifications(self, page: int, page_size: int) -> list[Notification | OneOffNotification]:
        return cast(
            list[Notification | OneOffNotification],
            self.__paginate_notifications(self.get_all_future_notifications(), page, page_size)
        )

    def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification | OneOffNotification]:
        return cast(list[Notification | OneOffNotification], self.__paginate_notifications(
            self.get_all_future_notifications_from_user(user_id), page, page_size
        ))

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

    def _convert_notification_to_json(self, notification: Notification | OneOffNotification) -> dict:
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
                "send_after": notification.send_after.isoformat() if notification.send_after else None,
                "subject_template": notification.subject_template,
                "preheader_template": notification.preheader_template,
                "status": notification.status,
                "context_used": notification.context_used,
                "adapter_extra_parameters": notification.adapter_extra_parameters,
                "is_one_off": True,
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
                "send_after": notification.send_after.isoformat() if notification.send_after else None,
                "subject_template": notification.subject_template,
                "preheader_template": notification.preheader_template,
                "status": notification.status,
                "context_used": notification.context_used,
                "adapter_extra_parameters": notification.adapter_extra_parameters,
                "is_one_off": False,
            }

    def _convert_json_to_notification(self, notification: dict) -> Notification | OneOffNotification:
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
            )

    def _store_notifications(self):
        json_output_file = open(self.database_file_name, "w", encoding="utf-8")
        json.dump(
            [self._convert_notification_to_json(n) for n in self.notifications],
            json_output_file,
        )
        json_output_file.close()

    def get_pending_notifications(self, page: int, page_size: int) -> list[Notification | OneOffNotification]:
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
        attachments: list[NotificationAttachment] | None = None,
    ) -> Notification:
        stored_attachments = self._store_attachments(attachments or [])

        notification = Notification(
            id=str(uuid.uuid4()),
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
        )
        self.notifications.append(notification)
        self._store_notifications()
        return notification

    def _store_attachments(self, attachments: list[NotificationAttachment]) -> list[StoredAttachment]:
        """Store attachments in memory and return StoredAttachment instances"""
        stored_attachments = []

        for attachment in attachments:
            # Read file data from various sources
            file_data = self._read_attachment_data(attachment.file)
            attachment_id = str(uuid.uuid4())

            # Create fake attachment file
            attachment_file = FakeFileAttachmentFile(file_data, attachment.filename)

            stored_attachment = StoredAttachment(
                id=attachment_id,
                filename=attachment.filename,
                content_type=attachment.content_type or 'application/octet-stream',
                size=len(file_data),
                checksum=hashlib.sha256(file_data).hexdigest(),
                created_at=datetime.datetime.now(tz=datetime.timezone.utc),
                file=attachment_file,
                description=attachment.description,
                is_inline=attachment.is_inline,
                storage_metadata={"storage_type": "in_memory"},
            )

            stored_attachments.append(stored_attachment)

        return stored_attachments

    def _read_attachment_data(self, file) -> bytes:
        """Read file data from various file-like object types"""
        from pathlib import Path

        if isinstance(file, bytes):
            return file
        if isinstance(file, str):
            if self._is_url(file):
                return self._download_from_url(file)
            else:
                # Read from file path
                with open(file, 'rb') as f:
                    return f.read()
        if isinstance(file, Path):
            with open(file, 'rb') as f:
                return f.read()
        if hasattr(file, 'read'):
            current_pos = file.tell() if hasattr(file, 'tell') else 0
            if hasattr(file, 'seek'):
                file.seek(0)
            data = file.read()
            if hasattr(file, 'seek'):
                file.seek(current_pos)
            if isinstance(data, str):
                return data.encode('utf-8')
            return data

        raise ValueError(f"Unsupported file type: {type(file)}")

    def _is_url(self, file_str: str) -> bool:
        """Check if a string is a URL"""
        return file_str.startswith(('http://', 'https://', 's3://', 'gs://', 'azure://'))

    def _download_from_url(self, url: str) -> bytes:
        """Download file content from URL (simplified for testing)"""
        # For testing purposes, return dummy data
        # In a real implementation, this would use requests or similar
        return f"Downloaded content from {url}".encode('utf-8')

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
        attachments: list[NotificationAttachment] | None = None,
    ) -> OneOffNotification:
        stored_attachments = self._store_attachments(attachments or [])

        notification = OneOffNotification(
            id=uuid.uuid4(),
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

    def mark_pending_as_sent(self, notification_id: int | str | uuid.UUID) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.SENT.value
        self._store_notifications()
        return notification

    def mark_pending_as_failed(self, notification_id: int | str | uuid.UUID) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.FAILED.value
        self._store_notifications()
        return notification

    def mark_sent_as_read(self, notification_id: int | str | uuid.UUID) -> Notification | OneOffNotification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.READ.value
        self._store_notifications()
        return notification

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
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
        return cast(list[Notification], self.__paginate_notifications(
            self.filter_all_in_app_unread_notifications(user_id), page, page_size
        ))

    def __paginate_notifications(
        self, notifications: list[Notification | OneOffNotification] | list[Notification], page: int, page_size: int
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
        try:
            os.remove(self.database_file_name)
        except FileNotFoundError:
            pass

    def _store_attachments(self, attachments: list[NotificationAttachment]) -> list[StoredAttachment]:
        """Store attachments in memory and return StoredAttachment instances"""
        stored_attachments = []

        for attachment in attachments:
            # Read file data from various sources
            file_data = self._read_attachment_data(attachment.file)
            attachment_id = str(uuid.uuid4())

            # Create fake attachment file
            attachment_file = FakeFileAttachmentFile(file_data, attachment.filename)

            stored_attachment = StoredAttachment(
                id=attachment_id,
                filename=attachment.filename,
                content_type=attachment.content_type or 'application/octet-stream',
                size=len(file_data),
                checksum=hashlib.sha256(file_data).hexdigest(),
                created_at=datetime.datetime.now(tz=datetime.timezone.utc),
                file=attachment_file,
                description=attachment.description,
                is_inline=attachment.is_inline,
                storage_metadata={"storage_type": "in_memory"},
            )

            stored_attachments.append(stored_attachment)

        return stored_attachments

    def _read_attachment_data(self, file) -> bytes:
        """Read file data from various file-like object types"""
        from pathlib import Path

        if isinstance(file, bytes):
            return file
        if isinstance(file, str):
            if self._is_url(file):
                return self._download_from_url(file)
            else:
                # Read from file path
                with open(file, 'rb') as f:
                    return f.read()
        if isinstance(file, Path):
            with open(file, 'rb') as f:
                return f.read()
        if hasattr(file, 'read'):
            current_pos = file.tell() if hasattr(file, 'tell') else 0
            if hasattr(file, 'seek'):
                file.seek(0)
            data = file.read()
            if hasattr(file, 'seek'):
                file.seek(current_pos)
            if isinstance(data, str):
                return data.encode('utf-8')
            return data

        raise ValueError(f"Unsupported file type: {type(file)}")

    def _is_url(self, file_str: str) -> bool:
        """Check if a string is a URL"""
        return file_str.startswith(('http://', 'https://', 's3://', 'gs://', 'azure://'))

    def _download_from_url(self, url: str) -> bytes:
        """Download file content from URL (simplified for testing)"""
        # For testing purposes, return dummy data
        # In a real implementation, this would use requests or similar
        return f"Downloaded content from {url}".encode('utf-8')

    async def get_future_notifications(self, page: int, page_size: int) -> list[Notification]:
        return cast(list[Notification], self.__paginate_notifications(
            await self.get_all_future_notifications(), page, page_size
        ))

    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification]:
        return cast(list[Notification], self.__paginate_notifications(
            await self.get_all_future_notifications_from_user(user_id), page, page_size
        ))

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

    def _convert_notification_to_json(self, notification: Notification | OneOffNotification) -> dict:
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
        }

        if isinstance(notification, OneOffNotification):
            base_dict.update({
                "email_or_phone": notification.email_or_phone,
                "first_name": notification.first_name,
                "last_name": notification.last_name,
            })
        else:
            base_dict["user_id"] = str(notification.user_id)

        return base_dict

    def _convert_json_to_notification(self, notification: dict) -> Notification | OneOffNotification:
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

    async def get_pending_notifications(self, page: int, page_size: int) -> list[Notification | OneOffNotification]:
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
        attachments: list[NotificationAttachment] | None = None,
        lock: asyncio.Lock | None = None,
    ) -> Notification:
        stored_attachments = self._store_attachments(attachments or [])

        notification = Notification(
            id=str(uuid.uuid4()),
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
        attachments: list[NotificationAttachment] | None = None,
        lock: asyncio.Lock | None = None,
    ) -> OneOffNotification:
        stored_attachments = self._store_attachments(attachments or [])

        notification = OneOffNotification(
            id=uuid.uuid4(),
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
        await self._store_notifications(lock)
        return notification

    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
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
        return cast(list[Notification], self.__paginate_notifications(in_app_unread_notifications, page, page_size))

    def __paginate_notifications(
        self, notifications: list[Notification | OneOffNotification] | list[Notification], page: int, page_size: int
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
