import datetime
import uuid
import pytest
from unittest.mock import patch
from freezegun import freeze_time
from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationError
from vintasend.services.dataclasses import Notification
from vintasend.services.notification_backends.stubs.fake_backend import FakeFileBackend
from vintasend.services.notification_service import NotificationService, register_context


class TestNotificationService:
    def setup_method(self, method):
        register_context("test_context")(self.create_notification_context)
        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

    def teardown_method(self, method):
        FakeFileBackend(database_file_name="service-tests-notifications.json").clear()

    def create_notification_context(self, test):
        if test != "test":
            raise ValueError()
        return {"test": "test"}

    def test_sends_without_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="non_registered_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            self.notification_service.send(notification)

        mocked_logger.exception.assert_called_once()

    def test_sends_with_context_error(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "not_test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        backend._store_notifications()

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            self.notification_service.send(notification)

        mocked_logger.exception.assert_called_once()

    def test_sends_with_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )

        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        backend._store_notifications()

        notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        notification_service.send(notification)

        assert len(notification_service.notification_adapters[0].sent_emails) == 1

    def test_create_notification(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 1

    def test_create_notification_with_send_after_in_the_future(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 0

    def test_create_notification_with_send_after_in_the_past(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 1

    def test_update_notification(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            title="Updated Test Notification",
        )

        assert updated_notification.title == "Updated Test Notification"
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 0

    def test_update_notification_changing_send_after_to_the_past(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        new_send_after = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1)
        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=new_send_after,
        )

        assert updated_notification.send_after == new_send_after
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 1

    def test_update_notification_changing_send_after_to_none(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=None,
        )

        assert updated_notification.send_after is None
        assert len(self.notification_service.notification_adapters[0].sent_emails) == 1

    def test_send_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with freeze_time(send_after + datetime.timedelta(days=1)):
            self.notification_service.send_pending_notifications()

        assert len(self.notification_service.notification_adapters[0].sent_emails) == 1

    def test_get_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 3",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with freeze_time(send_after):
            pending_notifications = self.notification_service.get_pending_notifications(
                page=1, page_size=1
            )
            assert len(list(pending_notifications)) == 1

            pending_notifications = self.notification_service.get_pending_notifications(
                page=2, page_size=1
            )
            assert len(list(pending_notifications)) == 1

            pending_notifications = self.notification_service.get_pending_notifications(
                page=3, page_size=1
            )
            assert len(list(pending_notifications)) == 0

    def test_get_notification(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert notification == retrieved_notification

    def test_get_notification_not_found(self):
        with pytest.raises(ValueError):
            self.notification_service.get_notification(uuid.uuid4())

    def test_mark_read(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        self.notification_service.mark_read(notification.id)

        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert retrieved_notification.status == NotificationStatus.READ.value

    def test_get_in_app_unread_without_a_in_app_adapter_configured(self):
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with pytest.raises(NotificationError):
            self.notification_service.get_in_app_unread(user_id=1)