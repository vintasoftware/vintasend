import datetime
import uuid
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import (
    NotificationError,
    NotificationMarkFailedError,
    NotificationMarkSentError,
    NotificationNotFoundError,
    NotificationSendError,
    NotificationUpdateError,
)
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_adapters.async_base import NotificationDict
from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncIOEmailAdapter,
    FakeEmailAdapter,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeTemplateRenderer,
)


def notification_to_dict(notification: "Notification") -> NotificationDict:
    return NotificationDict(
        id=notification.id if not isinstance(notification.id, uuid.UUID) else str(notification.id),
        user_id=notification.user_id if not isinstance(notification.user_id, uuid.UUID) else str(notification.user_id),
        notification_type=notification.notification_type,
        title=notification.title,
        body_template=notification.body_template,
        context_name=notification.context_name,
        context_kwargs={
            k: v if not isinstance(v, uuid.UUID) else str(v)
            for k, v in notification.context_kwargs.items()
        },
        send_after=notification.send_after.isoformat() if notification.send_after else None,
        subject_template=notification.subject_template,
        preheader_template=notification.preheader_template,
        status=notification.status,
        context_used=notification.context_used,
        adapter_extra_parameters=notification.adapter_extra_parameters,
    )

class NotificationServiceTestCase(TestCase):
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
        return NotificationContextDict({"test": "test"})

    def test_sends_without_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="non_registered_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            notification_backend=backend,
        )

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            notification_service.send(notification)

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

        notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend=backend,
        )
        
        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            notification_service.send(notification)

        mocked_logger.exception.assert_called_once()

    def test_sends_with_rendering_error(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        backend._store_notifications()

        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithException",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        with pytest.raises(NotificationSendError):
            self.notification_service.send(notification)

    def test_sends_with_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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

        assert len(list(notification_service.notification_adapters)[0].sent_emails) == 1

        sent_notification = notification_service.get_notification(notification.id)
        assert sent_notification.status == NotificationStatus.SENT.value
        assert sent_notification.context_used == {"test": "test"}

    def test_create_notification(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    @patch("vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend.mark_pending_as_sent")
    def test_create_notification_with_failing_mark_as_sent(self, mock_mark_pending_as_sent):
        assert len(self.notification_service.notification_backend.notifications) == 0
        mock_mark_pending_as_sent.side_effect = NotificationUpdateError()

        with pytest.raises(NotificationMarkSentError):
            self.notification_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

    @patch("vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend.mark_pending_as_failed")
    def test_create_notification_with_failing_mark_as_failed(self, mock_mark_pending_as_failed):
        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithException",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 0
        mock_mark_pending_as_failed.side_effect = NotificationUpdateError()

        with pytest.raises(NotificationMarkFailedError):
            self.notification_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

    def test_create_notification_with_send_after_in_the_future(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0

    def test_create_notification_with_send_after_in_the_past(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    def test_update_notification(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            title="Updated Test Notification",
        )

        assert updated_notification.title == "Updated Test Notification"
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0

    def test_update_notification_changing_send_after_to_the_past(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    def test_update_notification_changing_send_after_to_none(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=None,
        )

        assert updated_notification.send_after is None
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    def test_send_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with freeze_time(send_after + datetime.timedelta(days=1)):
            self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1
    
    @patch("vintasend.services.notification_service.NotificationService.send")
    def test_send_pending_notifications_counts_failed_notifications(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_send.side_effect = NotificationSendError()
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        mocked_logger.exception.assert_called_once()
    
    @patch("vintasend.services.notification_service.NotificationService.send")
    def test_send_pending_notifications_counts_failed_marking_notifications_as_failed(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_send.side_effect = NotificationMarkFailedError()
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        assert mocked_logger.exception.call_count == 2
    
    @patch("vintasend.services.notification_service.NotificationService.send")
    def test_send_pending_notifications_counts_failed_marking_notifications_as_sent(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_send.side_effect = NotificationMarkSentError()
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        mocked_logger.exception.assert_called_once()

    def test_get_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert notification == retrieved_notification

    def test_get_notification_not_found(self):
        with pytest.raises(NotificationNotFoundError):
            self.notification_service.get_notification(uuid.uuid4())

    def test_mark_read(self):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        self.notification_service.mark_read(notification.id)

        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert retrieved_notification.status == NotificationStatus.READ.value

    def test_get_in_app_unread_without_an_in_app_adapter_configured(self):
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with pytest.raises(NotificationError):
            self.notification_service.get_in_app_unread(user_id=1)

    def test_get_in_app_unread(self):
        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        in_app_notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        notifications = list(self.notification_service.get_in_app_unread(user_id=1))
        assert len(notifications) == 1
        assert (notifications)[0].id == in_app_notification.id


    @patch("vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter.send")
    def test_mark_notification_as_failed_if_sending_fails(self, mock_adapter_send):
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_adapter_send.side_effect = NotificationError()

        with pytest.raises(NotificationSendError):
            self.notification_service.send(notification)
        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert retrieved_notification.status == NotificationStatus.FAILED.value

    def test_cancel_notification(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications_before = self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications_before)) == 1

        self.notification_service.cancel_notification(notification.id)
        
        pending_notifications_after = self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications_after)) == 0

    def test_get_all_future_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
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
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications = self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications)) == 2

    def test_get_future_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification1 = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        notification2 = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications = self.notification_service.get_future_notifications(page=1, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification1.id

        pending_notifications = self.notification_service.get_future_notifications(page=2, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification2.id

        pending_notifications = self.notification_service.get_future_notifications(page=3, page_size=1)
        assert len(list(pending_notifications)) == 0

    def test_get_all_future_notifications_from_user(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        self.notification_service.create_notification(
            user_id=2,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications = self.notification_service.get_all_future_notifications_from_user(user_id=1)
        assert len(list(pending_notifications)) == 1

    def test_get_future_notifications_from_user(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification1 = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        notification2 = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # future notification from another user, not to be listed
        self.notification_service.create_notification(
            user_id=2,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 3",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications = self.notification_service.get_future_notifications_from_user(user_id=1, page=1, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification1.id

        pending_notifications = self.notification_service.get_future_notifications_from_user(user_id=1, page=2, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification2.id

        pending_notifications = self.notification_service.get_future_notifications_from_user(user_id=1, page=3, page_size=1)
        assert len(list(pending_notifications)) == 0

    def test_update_non_existing_notification(self):
        with pytest.raises(NotificationNotFoundError):
            self.notification_service.update_notification(
                notification_id=uuid.uuid4(),
                title="Updated Test Notification",
            )

    def test_fake_file_backend_handles_invalid_json_file(self):
        file = open("service-tests-notifications.json", "w")
        file.write("invalid json")
        file.close()
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
        assert self.notification_service.notification_backend.notifications == []

    def test_use_invalid_backend(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="invalid.backend",
                notification_backend_kwargs={},
            )
       
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                notification_backend_kwargs={},
            )

        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.InvalidBackend",
                notification_backend_kwargs={},
            )

    def test_use_invalid_adapter(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "invalid.adapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.InvalidAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )
    
    def test_use_invalid_template_renderer(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "invalid.template_renderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithExceptionOnInit",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.InvalidTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
                notification_backend_kwargs={},
            )

    def test_delayed_send(self):
        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 0

        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        self.notification_service.delayed_send(
            notification_to_dict(notification),
            {"test": "test"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    def test_delayed_send_with_unsupported_notification_type(self):
        self.notification_service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 0

        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.get_all_pending_notifications()) == 0

        with freeze_time(send_after + datetime.timedelta(days=1)):
            self.notification_service.delayed_send(
                notification_to_dict(notification),
                {"test": "test"},
            )

            assert len(self.notification_service.notification_backend.get_all_pending_notifications()) == 1

    def test_delayed_send_without_async_adapter(self):
        assert len(self.notification_service.notification_backend.notifications) == 0

        notification = self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        assert len(self.notification_service.notification_backend.notifications) == 1

        self.notification_service.delayed_send(
            notification_to_dict(notification),
            {"test": "test"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 1

    def test_instanciate_with_adapters_and_backend_instances_instead_of_string(self):
        notification_backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        notification_adapters = [
            FakeEmailAdapter(backend=notification_backend, template_renderer=FakeTemplateRenderer()),
        ]

        service = NotificationService(
            notification_adapters=notification_adapters,
            notification_backend=notification_backend,
        )

        assert service.notification_backend == notification_backend
        assert service.notification_adapters == notification_adapters


class AsyncIONotificationServiceTestCase(IsolatedAsyncioTestCase):
    def setup_method(self, method):
        register_context("test_context")(self.create_notification_context)
        self.notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

    def teardown_method(self, method):
        FakeFileBackend(database_file_name="service-tests-notifications.json").clear()

    def teardown_class(self) -> None:
        FakeFileBackend(database_file_name="service-tests-notifications.json").clear()

    def create_notification_context(self, test):
        if test != "test":
            raise ValueError()
        return NotificationContextDict({"test": "test"})

    @pytest.mark.asyncio
    async def test_sends_without_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="non_registered_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )

        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        await backend._store_notifications()

        notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend=backend,
        )

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            await notification_service.send(notification)

        mocked_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_with_context_error(self):
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
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        await backend._store_notifications()
          
        notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend=backend,
        )
        
        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            await notification_service.send(notification)

        mocked_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_with_rendering_error(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        await backend._store_notifications()

        self.notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithException",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        with pytest.raises(NotificationSendError):
            await self.notification_service.send(notification)

    @pytest.mark.asyncio
    async def test_sends_with_context(self):
        notification = Notification(
            id=str(uuid.uuid4()),
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            status=NotificationStatus.PENDING_SEND.value,
        )

        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        backend.notifications.append(notification)
        await backend._store_notifications()

        notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        await notification_service.send(notification)

        assert len(list(notification_service.notification_adapters)[0].sent_emails) == 1

        sent_notification = await notification_service.get_notification(notification.id)
        assert sent_notification.status == NotificationStatus.SENT.value
        assert sent_notification.context_used == {"test": "test"}

    @pytest.mark.asyncio
    async def test_create_notification(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    @pytest.mark.asyncio
    @patch("vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend.mark_pending_as_sent")
    async def test_create_notification_with_failing_mark_as_sent(self, mock_mark_pending_as_sent):
        assert len(self.notification_service.notification_backend.notifications) == 0
        mock_mark_pending_as_sent.side_effect = NotificationUpdateError()

        with pytest.raises(NotificationMarkSentError):
            await self.notification_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

    @pytest.mark.asyncio
    @patch("vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend.mark_pending_as_failed")
    async def test_create_notification_with_failing_mark_as_failed(self, mock_mark_pending_as_failed):
        self.notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithException",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        assert len(self.notification_service.notification_backend.notifications) == 0
        mock_mark_pending_as_failed.side_effect = NotificationUpdateError()

        with pytest.raises(NotificationMarkFailedError):
            await self.notification_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

    @pytest.mark.asyncio
    async def test_create_notification_with_send_after_in_the_future(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0

    @pytest.mark.asyncio
    async def test_create_notification_with_send_after_in_the_past(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert notification == self.notification_service.notification_backend.notifications[0]
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    @pytest.mark.asyncio
    async def test_update_notification(self):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = await self.notification_service.update_notification(
            notification_id=notification.id,
            title="Updated Test Notification",
        )

        assert updated_notification.title == "Updated Test Notification"
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0

    @pytest.mark.asyncio
    async def test_update_notification_changing_send_after_to_the_past(self):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        new_send_after = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=1)
        updated_notification = await self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=new_send_after,
        )

        assert updated_notification.send_after == new_send_after
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    @pytest.mark.asyncio
    async def test_update_notification_changing_send_after_to_none(self):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        updated_notification = await self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=None,
        )

        assert updated_notification.send_after is None
        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1

    @pytest.mark.asyncio
    async def test_send_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with freeze_time(send_after + datetime.timedelta(days=1)):
            await self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 1
    
    @pytest.mark.asyncio
    @patch("vintasend.services.notification_service.AsyncIONotificationService.send")
    async def test_send_pending_notifications_counts_failed_notifications(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        async def mock_send_side_effect(*_args, **_kwargs):
            raise NotificationSendError()
        
        mock_send.side_effect = mock_send_side_effect
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                await self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        mocked_logger.exception.assert_called_once()
    
    @pytest.mark.asyncio
    @patch("vintasend.services.notification_service.AsyncIONotificationService.send")
    async def test_send_pending_notifications_counts_failed_marking_notifications_as_failed(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_send.side_effect = NotificationMarkFailedError()
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                await self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        assert mocked_logger.exception.call_count == 2
    
    @pytest.mark.asyncio
    @patch("vintasend.services.notification_service.AsyncIONotificationService.send")
    async def test_send_pending_notifications_counts_failed_marking_notifications_as_sent(self, mock_send):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_send.side_effect = NotificationMarkSentError()
        with freeze_time(send_after + datetime.timedelta(days=1)):
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                await self.notification_service.send_pending_notifications()

        assert len(list(self.notification_service.notification_adapters)[0].sent_emails) == 0
        mocked_logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_pending_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 3",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with freeze_time(send_after):
            pending_notifications = await self.notification_service.get_pending_notifications(
                page=1, page_size=1
            )
            assert len(list(pending_notifications)) == 1

            pending_notifications = await self.notification_service.get_pending_notifications(
                page=2, page_size=1
            )
            assert len(list(pending_notifications)) == 1

            pending_notifications = await self.notification_service.get_pending_notifications(
                page=3, page_size=1
            )
            assert len(list(pending_notifications)) == 0

    @pytest.mark.asyncio
    async def test_get_notification(self):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        retrieved_notification = await self.notification_service.get_notification(notification.id)
        assert notification == retrieved_notification

    @pytest.mark.asyncio
    async def test_get_notification_not_found(self):
        with pytest.raises(NotificationNotFoundError):
            await self.notification_service.get_notification(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_mark_read(self):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        await self.notification_service.mark_read(notification.id)

        retrieved_notification = await self.notification_service.get_notification(notification.id)
        assert retrieved_notification.status == NotificationStatus.READ.value

    @pytest.mark.asyncio
    async def test_get_in_app_unread_without_an_in_app_adapter_configured(self):
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with pytest.raises(NotificationError):
            await self.notification_service.get_in_app_unread(user_id=1)

    @pytest.mark.asyncio
    @patch("vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter.send")
    async def test_mark_notification_as_failed_if_sending_fails(self, mock_adapter_send):
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        mock_adapter_send.side_effect = NotificationError()

        with pytest.raises(NotificationSendError):
            await self.notification_service.send(notification)
        retrieved_notification = await self.notification_service.get_notification(notification.id)
        assert retrieved_notification.status == NotificationStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_cancel_notification(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications_before = await self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications_before)) == 1

        await self.notification_service.cancel_notification(notification.id)
        
        pending_notifications_after = await self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications_after)) == 0

    @pytest.mark.asyncio
    async def test_get_all_future_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        pending_notifications = await self.notification_service.get_all_future_notifications()
        assert len(list(pending_notifications)) == 2

    @pytest.mark.asyncio
    async def test_get_future_notifications(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification1 = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )
        notification2 = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        pending_notifications = await self.notification_service.get_future_notifications(page=1, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification1.id

        pending_notifications = await self.notification_service.get_future_notifications(page=2, page_size=1)
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification2.id

        pending_notifications = await self.notification_service.get_future_notifications(page=3, page_size=1)
        assert len(list(pending_notifications)) == 0

    @pytest.mark.asyncio
    async def test_get_all_future_notifications_from_user(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )
        await self.notification_service.create_notification(
            user_id=2,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        pending_notifications = await self.notification_service.get_all_future_notifications_from_user(user_id=1)
        assert len(list(pending_notifications)) == 1

    @pytest.mark.asyncio
    async def test_get_future_notifications_from_user(self):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        notification1 = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 1",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )
        notification2 = await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 2",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # future notification from another user, not to be listed
        await self.notification_service.create_notification(
            user_id=2,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification 3",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after + datetime.timedelta(days=3),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # pending notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Send Immediately Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        # delayed notification, not to be listed
        await self.notification_service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Delayed Notification",
            body_template="vintasend_django/emails/test.test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after - datetime.timedelta(days=10),
            subject_template="vintasend_django/emails/test.test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test.test_templated_email_preheader.html",
        )

        pending_notifications = await self.notification_service.get_future_notifications_from_user(
            user_id=1, page=1, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification1.id

        pending_notifications = await self.notification_service.get_future_notifications_from_user(
            user_id=1, page=2, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert list(pending_notifications)[0].id == notification2.id

        pending_notifications = await self.notification_service.get_future_notifications_from_user(
            user_id=1, page=3, page_size=1
        )
        assert len(list(pending_notifications)) == 0

    @pytest.mark.asyncio
    async def test_update_non_existing_notification(self):
        with pytest.raises(NotificationNotFoundError):
            await self.notification_service.update_notification(
                notification_id=uuid.uuid4(),
                title="Updated Test Notification",
            )

    @pytest.mark.asyncio
    async def test_fake_file_backend_handles_invalid_json_file(self):
        file = open("service-tests-notifications.json", "w")
        file.write("invalid json")
        file.close()
        self.notification_service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )
        assert self.notification_service.notification_backend.notifications == []

    @pytest.mark.asyncio
    async def test_use_invalid_backend(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="invalid.backend",
                notification_backend_kwargs={},
            )
       
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                notification_backend_kwargs={},
            )

        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.InvalidBackend",
                notification_backend_kwargs={},
            )

    @pytest.mark.asyncio
    async def test_use_invalid_adapter(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "invalid.adapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.InvalidAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )
    
    @pytest.mark.asyncio
    async def test_use_invalid_template_renderer(self):
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "invalid.template_renderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRendererWithExceptionOnInit",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )
        with pytest.raises(ValueError):
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.InvalidTemplateRenderer",
                    )
                ],
                notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
                notification_backend_kwargs={},
            )

    @pytest.mark.asyncio
    async def test_instanciate_with_adapters_and_backend_instances_instead_of_string(self):
        notification_backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        notification_adapters = [
            FakeAsyncIOEmailAdapter(backend=notification_backend, template_renderer=FakeTemplateRenderer()),
        ]
        service = AsyncIONotificationService(
            notification_adapters=notification_adapters,
            notification_backend=notification_backend,
        )

        assert service.notification_backend == notification_backend
        assert service.notification_adapters == notification_adapters

