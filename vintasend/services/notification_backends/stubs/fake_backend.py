import datetime
import json
import os
import uuid

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.services.dataclasses import Notification, UpdateNotificationKwargs
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.exceptions import NotificationNotFoundError


class FakeFileBackend(BaseNotificationBackend):
    notifications: list[Notification]

    def __init__(self, database_file_name: str = "notifications.json"):
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

    def get_future_notifications(self, page: int, page_size: int) -> list[Notification]:
        return self.__paginate_notifications(self.get_all_future_notifications(), page, page_size)

    def get_future_notifications_from_user(self, user_id: int | str | uuid.UUID, page: int, page_size: int) -> list[Notification]:
        return self.__paginate_notifications(self.get_all_future_notifications_from_user(user_id), page, page_size)

    def get_all_future_notifications(self) -> list[Notification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (n.send_after is not None and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc))
        ]

    def get_all_future_notifications_from_user(self, user_id: int | str | uuid.UUID) -> list[Notification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (n.send_after is not None and n.send_after > datetime.datetime.now(tz=datetime.timezone.utc))
            and str(n.user_id) == str(user_id)
        ]

    def get_all_pending_notifications(self) -> list[Notification]:
        return [
            n
            for n in self.notifications
            if n.status == NotificationStatus.PENDING_SEND.value
            and (n.send_after is None or n.send_after <= datetime.datetime.now(tz=datetime.timezone.utc))
        ]

    def _convert_notification_to_json(self, notification: Notification) -> dict:
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
        }

    def _convert_json_to_notification(self, notification: dict) -> Notification:
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
        )

    def _store_notifications(self):
        json_output_file = open(self.database_file_name, "w", encoding="utf-8")
        json.dump(
            [self._convert_notification_to_json(n) for n in self.notifications],
            json_output_file,
        )
        json_output_file.close()

    def get_pending_notifications(self, page: int, page_size: int) -> list[Notification]:
        # page is 1-indexed
        return self.get_all_pending_notifications()[
            ((page - 1) * page_size) : ((page - 1) * page_size) + page_size
        ]

    def persist_notification(
        self,
        user_id: uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
    ) -> Notification:
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
        )
        self.notifications.append(notification)
        self._store_notifications()
        return notification

    def persist_notification_update(
        self, notification_id: int | str | uuid.UUID, **kwargs: UpdateNotificationKwargs
    ) -> Notification:
        notification = self.get_notification(notification_id)

        for key, value in kwargs.items():
            setattr(notification, key, value)

        return notification

    def mark_pending_as_sent(self, notification_id: int | str | uuid.UUID) -> Notification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.SENT.value
        self._store_notifications()
        return notification

    def mark_pending_as_failed(self, notification_id: int | str | uuid.UUID) -> Notification:
        notification = self.get_notification(notification_id)
        notification.status = NotificationStatus.FAILED.value
        self._store_notifications()
        return notification

    def mark_sent_as_read(self, notification_id: int | str | uuid.UUID) -> Notification:
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
    ) -> Notification:
        try:
            return next(n for n in self.notifications if str(n.id) == str(notification_id))
        except StopIteration as e:
            raise NotificationNotFoundError("Notification not found") from e

    def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> list[Notification]:
        notifications = [
            n
            for n in self.notifications
            if n.user_id == user_id
            and n.status == NotificationStatus.SENT.value
            and n.notification_type == NotificationTypes.IN_APP.value
        ]
        return notifications

    def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> list[Notification]:
        return self.__paginate_notifications(self.filter_all_in_app_unread_notifications(user_id), page, page_size)

    def __paginate_notifications(self, notifications: list[Notification], page: int, page_size: int):
        # page is 1-indexed
        return notifications[((page - 1) * page_size) : ((page - 1) * page_size) + page_size]
    
    def get_user_email_from_notification(self, notification_id: int | str | uuid.UUID) -> str:
        notification = self.get_notification(notification_id)
        return notification.context_kwargs.get("email", "testemail@example.com")
    

class InvalidBackend():
    def __init__(self, *_args, **_kwargs):
        pass