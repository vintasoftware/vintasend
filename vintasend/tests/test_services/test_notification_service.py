import datetime
import inspect
import subprocess
import sys
import uuid
from types import MappingProxyType
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from vintasend.app_settings import NotificationSettings
from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import (
    DuplicateNotificationAdapterError,
    InvalidOneOffNotificationRecipientError,
    NotificationError,
    NotificationMarkFailedError,
    NotificationMarkSentError,
    NotificationNotFoundError,
    NotificationSendError,
    NotificationUpdateError,
    TenantReassignmentError,
)
from vintasend.services.dataclasses import Notification, NotificationContextDict, OneOffNotification
from vintasend.services.notification_adapters.async_base import NotificationDict
from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncEmailAdapter,
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
        user_id=notification.user_id
        if not isinstance(notification.user_id, uuid.UUID)
        else str(notification.user_id),
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


def _reset_notification_settings_singleton(test_case: TestCase) -> None:
    """Clear the NotificationSettings singleton for one test, then restore it.

    NotificationSettings uses SingletonMeta: the first construction wins, and every later
    `config` argument is ignored. SingletonMeta.__call__ stores the built instance on an
    `_instances` attribute it sets directly on the class being constructed. Once
    NotificationSettings has been built once, that per-class attribute shadows the empty
    default living on SingletonMeta itself, so clearing SingletonMeta's own `_instances` has
    no effect at that point. NotificationSettings's own `_instances` attribute is the one
    that must be cleared, and it must be restored after the test. This state is process-global,
    so leaking it would make other tests order-dependent.
    """
    sentinel = object()
    original = vars(NotificationSettings).get("_instances", sentinel)

    def _restore() -> None:
        if original is sentinel:
            if "_instances" in vars(NotificationSettings):
                delattr(NotificationSettings, "_instances")
        else:
            NotificationSettings._instances = original

    test_case.addCleanup(_restore)
    NotificationSettings._instances = MappingProxyType({})


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

        assert len(next(iter(notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    def test_create_one_off_notification(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        one_off_notification = self.notification_service.create_one_off_notification(
            email_or_phone="test@example.com",
            first_name="John",
            last_name="Doe",
            notification_type=NotificationTypes.EMAIL.value,
            title="Test One-Off Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert (
            one_off_notification == self.notification_service.notification_backend.notifications[0]
        )
        assert isinstance(one_off_notification, OneOffNotification)
        assert one_off_notification.email_or_phone == "test@example.com"
        assert one_off_notification.first_name == "John"
        assert one_off_notification.last_name == "Doe"
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    def test_create_one_off_notification_with_empty_email_or_phone_raises_and_persists_nothing(
        self,
    ):
        assert len(self.notification_service.notification_backend.notifications) == 0

        with pytest.raises(InvalidOneOffNotificationRecipientError):
            self.notification_service.create_one_off_notification(
                email_or_phone="",
                first_name="John",
                last_name="Doe",
                notification_type=NotificationTypes.EMAIL.value,
                title="Test One-Off Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

        assert len(self.notification_service.notification_backend.notifications) == 0

    def test_create_one_off_notification_with_valid_phone_succeeds(self):
        assert len(self.notification_service.notification_backend.notifications) == 0

        one_off_notification = self.notification_service.create_one_off_notification(
            email_or_phone="+1234567890",
            first_name="Jane",
            last_name="Smith",
            notification_type=NotificationTypes.EMAIL.value,
            title="Test One-Off Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert one_off_notification.email_or_phone == "+1234567890"

    def test_create_notification_persists_tenant(self):
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
            tenant="tenant-a",
        )

        assert notification.tenant == "tenant-a"
        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert retrieved_notification.tenant == "tenant-a"

    def test_create_notification_without_tenant_defaults_to_none(self):
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

        assert notification.tenant is None

    @patch(
        "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend.mark_pending_as_sent"
    )
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

    @patch(
        "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend.mark_pending_as_failed"
    )
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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0

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

        new_send_after = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
            days=1
        )
        updated_notification = self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=new_send_after,
        )

        assert updated_notification.send_after == new_send_after
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    def test_update_notification_with_tenant_raises_tenant_reassignment_error(self):
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
            tenant="tenant-a",
        )

        with pytest.raises(TenantReassignmentError):
            self.notification_service.update_notification(
                notification_id=notification.id,
                tenant="tenant-b",  # type: ignore[call-arg]
            )

        unchanged_notification = self.notification_service.get_notification(notification.id)
        assert unchanged_notification.tenant == "tenant-a"

    def test_update_notification_with_tenant_via_raw_kwargs_raises(self):
        """Even bypassing the TypedDict via a plain dict must still raise.

        UpdateNotificationKwargs is not enforced at runtime, so update_notification must
        check the raw kwargs dict for "tenant" rather than relying on the TypedDict.
        """
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
            tenant="tenant-a",
        )
        untyped_kwargs: dict = {"title": "New title", "tenant": "tenant-b"}

        with pytest.raises(TenantReassignmentError):
            self.notification_service.update_notification(notification.id, **untyped_kwargs)

        unchanged_notification = self.notification_service.get_notification(notification.id)
        assert unchanged_notification.tenant == "tenant-a"
        assert unchanged_notification.title == "Test Notification"

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
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                self.notification_service.send_pending_notifications()

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1
        mocked_logger.info.assert_any_call("Sent %s notifications", 1)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 0)

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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        mocked_logger.exception.assert_called_once()
        mocked_logger.info.assert_any_call("Sent %s notifications", 0)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 1)

    @patch("vintasend.services.notification_service.NotificationService.send")
    def test_send_pending_notifications_counts_failed_marking_notifications_as_failed(
        self, mock_send
    ):
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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        assert mocked_logger.exception.call_count == 2
        mocked_logger.info.assert_any_call("Sent %s notifications", 0)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 1)

    @patch("vintasend.services.notification_service.NotificationService.send")
    def test_send_pending_notifications_counts_failed_marking_notifications_as_sent(
        self, mock_send
    ):
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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        mocked_logger.exception.assert_called_once()
        mocked_logger.info.assert_any_call("Sent %s notifications", 1)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 0)

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

    def test_mark_read_sets_read_at(self):
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
        assert notification.read_at is None

        self.notification_service.mark_read(notification.id)

        retrieved_notification = self.notification_service.get_notification(notification.id)
        assert retrieved_notification.read_at is not None

    def test_sent_at_is_none_until_sent_then_set(self):
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
        assert notification.sent_at is None

        self.notification_service.send(notification)

        sent_notification = self.notification_service.get_notification(notification.id)
        assert sent_notification.sent_at is not None

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

    def _in_app_service(self):
        return NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

    def _create_in_app(self, service, user_id=1):
        return service.create_notification(
            user_id=user_id,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

    def test_get_in_app_notifications_returns_read_and_unread_paginated(self):
        service = self._in_app_service()
        created = [self._create_in_app(service) for _ in range(3)]
        # Mark one as read -- it must still be returned by the "all" listing.
        service.mark_read(created[0].id)

        page1 = list(service.get_in_app_notifications(user_id=1, page=1, page_size=2))
        page2 = list(service.get_in_app_notifications(user_id=1, page=2, page_size=2))

        assert len(page1) == 2
        assert len(page2) == 1
        all_ids = {n.id for n in page1 + page2}
        assert all_ids == {n.id for n in created}

    def test_get_in_app_notifications_count(self):
        service = self._in_app_service()
        created = [self._create_in_app(service) for _ in range(3)]
        service.mark_read(created[0].id)

        assert service.get_in_app_notifications_count(user_id=1) == 3
        assert service.get_in_app_unread_count(user_id=1) == 2

    def test_in_app_list_and_count_require_in_app_adapter(self):
        # self.notification_service only has an email adapter configured.
        with pytest.raises(NotificationError):
            self.notification_service.get_in_app_notifications(user_id=1)
        with pytest.raises(NotificationError):
            self.notification_service.get_in_app_notifications_count(user_id=1)
        with pytest.raises(NotificationError):
            self.notification_service.get_in_app_unread_count(user_id=1)

    def test_mark_read_bulk_marks_sent_as_read(self):
        service = self._in_app_service()
        created = [self._create_in_app(service) for _ in range(3)]

        result = list(service.mark_read_bulk([n.id for n in created]))

        assert {n.id for n in result} == {n.id for n in created}
        assert all(n.status == NotificationStatus.READ.value for n in result)
        assert service.get_in_app_unread_count(user_id=1) == 0

    def test_mark_read_bulk_is_idempotent(self):
        service = self._in_app_service()
        created = [self._create_in_app(service) for _ in range(2)]
        ids = [n.id for n in created]

        service.mark_read_bulk(ids)
        # Re-marking already-read ids must not raise and must return final state.
        result = list(service.mark_read_bulk(ids))
        assert {n.id for n in result} == set(ids)
        assert all(n.status == NotificationStatus.READ.value for n in result)

    def test_mark_read_bulk_scopes_to_user(self):
        service = self._in_app_service()
        own = self._create_in_app(service, user_id=1)
        foreign = self._create_in_app(service, user_id=2)

        result = list(service.mark_read_bulk([own.id, foreign.id], user_id=1))

        assert {n.id for n in result} == {own.id}
        # Foreign notification stays unread/SENT.
        assert service.get_notification(foreign.id).status == NotificationStatus.SENT.value

    def test_mark_read_bulk_mixes_sent_read_and_missing(self):
        service = self._in_app_service()
        already_read = self._create_in_app(service)
        service.mark_read(already_read.id)
        sent = self._create_in_app(service)
        missing_id = str(uuid.uuid4())

        result = list(service.mark_read_bulk([already_read.id, sent.id, missing_id]))

        assert {n.id for n in result} == {already_read.id, sent.id}
        assert all(n.status == NotificationStatus.READ.value for n in result)

    def test_mark_read_bulk_sets_read_at_on_affected_rows_only(self):
        service = self._in_app_service()
        already_read = self._create_in_app(service)
        service.mark_read(already_read.id)
        already_read_at = service.get_notification(already_read.id).read_at

        newly_read = self._create_in_app(service)
        skipped = self._create_in_app(service)
        assert newly_read.read_at is None
        assert skipped.read_at is None

        result = list(service.mark_read_bulk([already_read.id, newly_read.id]))

        assert {n.id for n in result} == {already_read.id, newly_read.id}
        newly_read_notification = service.get_notification(newly_read.id)
        assert newly_read_notification.read_at is not None
        # Already-read rows are left untouched.
        assert service.get_notification(already_read.id).read_at == already_read_at
        # Notifications not passed to mark_read_bulk are never touched.
        assert service.get_notification(skipped.id).read_at is None

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

        pending_notifications = self.notification_service.get_future_notifications(
            page=1, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification1.id

        pending_notifications = self.notification_service.get_future_notifications(
            page=2, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification2.id

        pending_notifications = self.notification_service.get_future_notifications(
            page=3, page_size=1
        )
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

        pending_notifications = self.notification_service.get_all_future_notifications_from_user(
            user_id=1
        )
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

        pending_notifications = self.notification_service.get_future_notifications_from_user(
            user_id=1, page=1, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification1.id

        pending_notifications = self.notification_service.get_future_notifications_from_user(
            user_id=1, page=2, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification2.id

        pending_notifications = self.notification_service.get_future_notifications_from_user(
            user_id=1, page=3, page_size=1
        )
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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

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
                ),
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

        assert (
            len(self.notification_service.notification_backend.get_all_pending_notifications()) == 0
        )

        with freeze_time(send_after + datetime.timedelta(days=1)):
            self.notification_service.delayed_send(
                notification_to_dict(notification),
                {"test": "test"},
            )

            assert (
                len(self.notification_service.notification_backend.get_all_pending_notifications())
                == 1
            )

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
        notification_backend = FakeFileBackend(
            database_file_name="service-tests-notifications.json"
        )
        notification_adapters = [
            FakeEmailAdapter(
                backend=notification_backend, template_renderer=FakeTemplateRenderer()
            ),
        ]

        service = NotificationService(
            notification_adapters=notification_adapters,
            notification_backend=notification_backend,
        )

        assert service.notification_backend == notification_backend
        assert service.notification_adapters == notification_adapters

    def test_instances_config_initializes_notification_settings_singleton(self):
        """Config must reach NotificationSettings even when backend/adapters are instances.

        Neither get_notification_backend nor BaseNotificationAdapter.__init__ runs on that
        path, so NotificationService.__init__ has to construct NotificationSettings(config)
        itself, or the config argument is silently dropped and the app falls back to
        framework defaults.
        """
        _reset_notification_settings_singleton(self)

        class _FakeConfig:
            NOTIFICATION_DEFAULT_FROM_EMAIL = "sync-singleton-test@example.com"

        notification_backend = FakeFileBackend(
            database_file_name="service-tests-notifications.json"
        )
        notification_adapters = [
            FakeEmailAdapter(
                backend=notification_backend, template_renderer=FakeTemplateRenderer()
            ),
        ]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            NotificationService(
                notification_adapters=notification_adapters,
                notification_backend=notification_backend,
                config=_FakeConfig(),
            )

        assert (
            NotificationSettings().NOTIFICATION_DEFAULT_FROM_EMAIL
            == "sync-singleton-test@example.com"
        )

        # First construction wins: a later call with a different config is ignored, because
        # the singleton is already built and SingletonMeta returns the cached instance
        # without re-running __init__.
        NotificationSettings(object())
        assert (
            NotificationSettings().NOTIFICATION_DEFAULT_FROM_EMAIL
            == "sync-singleton-test@example.com"
        )

    def test_constructor_rejects_duplicate_adapter_notification_types(self):
        """Test that constructing with duplicate adapter types raises DuplicateNotificationAdapterError."""
        _reset_notification_settings_singleton(self)
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        adapter1 = FakeEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )
        adapter2 = FakeAsyncEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )

        with pytest.raises(DuplicateNotificationAdapterError) as exc_info:
            NotificationService(
                notification_adapters=[adapter1, adapter2],
                notification_backend=backend,
            )

        error_message = str(exc_info.value)
        assert "Duplicate adapter notification types are not allowed" in error_message
        assert "EMAIL" in error_message
        assert adapter1.adapter_import_str in error_message
        assert adapter2.adapter_import_str in error_message

    def test_constructor_rejects_duplicate_adapter_notification_types_from_import_strings(self):
        """Test that duplicates arriving as (import_str, renderer_str) tuples are also rejected."""
        _reset_notification_settings_singleton(self)
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")

        with pytest.raises(DuplicateNotificationAdapterError) as exc_info:
            NotificationService(
                notification_adapters=[
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    ),
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    ),
                ],
                notification_backend=backend,
            )

        error_message = str(exc_info.value)
        assert "Duplicate adapter notification types are not allowed" in error_message
        assert "EMAIL" in error_message
        assert (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter"
            in error_message
        )
        assert (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter"
            in error_message
        )

    def test_constructor_allows_single_adapter(self):
        """Test that constructing with a single adapter works fine."""
        _reset_notification_settings_singleton(self)
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")
        adapter = FakeEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )

        service = NotificationService(
            notification_adapters=[adapter],
            notification_backend=backend,
        )
        assert [a.notification_type for a in service.notification_adapters] == [
            NotificationTypes.EMAIL
        ]

    def test_constructor_allows_different_adapter_types(self):
        """Test that constructing with adapters of different types works fine."""
        _reset_notification_settings_singleton(self)
        backend = FakeFileBackend(database_file_name="service-tests-notifications.json")

        service = NotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
            ],
            notification_backend=backend,
        )
        assert [a.notification_type for a in service.notification_adapters] == [
            NotificationTypes.EMAIL,
            NotificationTypes.IN_APP,
        ]


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

        assert len(next(iter(notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    @pytest.mark.asyncio
    async def test_create_one_off_notification(self):
        assert len(self.notification_service.notification_backend.notifications) == 0
        one_off_notification = await self.notification_service.create_one_off_notification(
            email_or_phone="test@example.com",
            first_name="John",
            last_name="Doe",
            notification_type=NotificationTypes.EMAIL.value,
            title="Test One-Off Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert (
            one_off_notification == self.notification_service.notification_backend.notifications[0]
        )
        assert isinstance(one_off_notification, OneOffNotification)
        assert one_off_notification.email_or_phone == "test@example.com"
        assert one_off_notification.first_name == "John"
        assert one_off_notification.last_name == "Doe"
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    @pytest.mark.asyncio
    async def test_create_one_off_notification_with_empty_email_or_phone_raises_and_persists_nothing(
        self,
    ):
        assert len(self.notification_service.notification_backend.notifications) == 0

        with pytest.raises(InvalidOneOffNotificationRecipientError):
            await self.notification_service.create_one_off_notification(
                email_or_phone="",
                first_name="John",
                last_name="Doe",
                notification_type=NotificationTypes.EMAIL.value,
                title="Test One-Off Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="test_context",
                context_kwargs=NotificationContextDict({"test": "test"}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )

        assert len(self.notification_service.notification_backend.notifications) == 0

    @pytest.mark.asyncio
    async def test_create_one_off_notification_with_valid_phone_succeeds(self):
        assert len(self.notification_service.notification_backend.notifications) == 0

        one_off_notification = await self.notification_service.create_one_off_notification(
            email_or_phone="+1234567890",
            first_name="Jane",
            last_name="Smith",
            notification_type=NotificationTypes.EMAIL.value,
            title="Test One-Off Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert len(self.notification_service.notification_backend.notifications) == 1
        assert one_off_notification.email_or_phone == "+1234567890"

    @pytest.mark.asyncio
    async def test_create_notification_persists_tenant(self):
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
            tenant="tenant-a",
        )

        assert notification.tenant == "tenant-a"
        retrieved_notification = await self.notification_service.get_notification(notification.id)
        assert retrieved_notification.tenant == "tenant-a"

    @pytest.mark.asyncio
    async def test_create_notification_without_tenant_defaults_to_none(self):
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

        assert notification.tenant is None

    @pytest.mark.asyncio
    @patch(
        "vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend.mark_pending_as_sent"
    )
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
    @patch(
        "vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend.mark_pending_as_failed"
    )
    async def test_create_notification_with_failing_mark_as_failed(
        self, mock_mark_pending_as_failed
    ):
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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0

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

        new_send_after = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
            days=1
        )
        updated_notification = await self.notification_service.update_notification(
            notification_id=notification.id,
            send_after=new_send_after,
        )

        assert updated_notification.send_after == new_send_after
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

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
        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1

    @pytest.mark.asyncio
    async def test_update_notification_with_tenant_raises_tenant_reassignment_error(self):
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
            tenant="tenant-a",
        )

        with pytest.raises(TenantReassignmentError):
            await self.notification_service.update_notification(
                notification_id=notification.id,
                tenant="tenant-b",  # type: ignore[call-arg]
            )

        unchanged_notification = await self.notification_service.get_notification(notification.id)
        assert unchanged_notification.tenant == "tenant-a"

    @pytest.mark.asyncio
    async def test_update_notification_with_tenant_via_raw_kwargs_raises(self):
        """Even bypassing the TypedDict via a plain dict must still raise.

        UpdateNotificationKwargs is not enforced at runtime, so update_notification must
        check the raw kwargs dict for "tenant" rather than relying on the TypedDict.
        """
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
            tenant="tenant-a",
        )
        untyped_kwargs: dict = {"title": "New title", "tenant": "tenant-b"}

        with pytest.raises(TenantReassignmentError):
            await self.notification_service.update_notification(notification.id, **untyped_kwargs)

        unchanged_notification = await self.notification_service.get_notification(notification.id)
        assert unchanged_notification.tenant == "tenant-a"
        assert unchanged_notification.title == "Test Notification"

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
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                await self.notification_service.send_pending_notifications()

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 1
        mocked_logger.info.assert_any_call("Sent %s notifications", 1)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 0)

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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        mocked_logger.exception.assert_called_once()
        mocked_logger.info.assert_any_call("Sent %s notifications", 0)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 1)

    @pytest.mark.asyncio
    @patch("vintasend.services.notification_service.AsyncIONotificationService.send")
    async def test_send_pending_notifications_counts_failed_marking_notifications_as_failed(
        self, mock_send
    ):
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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        assert mocked_logger.exception.call_count == 2
        mocked_logger.info.assert_any_call("Sent %s notifications", 0)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 1)

    @pytest.mark.asyncio
    @patch("vintasend.services.notification_service.AsyncIONotificationService.send")
    async def test_send_pending_notifications_counts_failed_marking_notifications_as_sent(
        self, mock_send
    ):
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

        assert len(next(iter(self.notification_service.notification_adapters)).sent_emails) == 0
        mocked_logger.exception.assert_called_once()
        mocked_logger.info.assert_any_call("Sent %s notifications", 1)
        mocked_logger.info.assert_any_call("Failed to send %s notifications", 0)

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
    async def test_mark_read_sets_read_at(self):
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
        assert notification.read_at is None

        await self.notification_service.mark_read(notification.id)

        retrieved_notification = await self.notification_service.get_notification(notification.id)
        assert retrieved_notification.read_at is not None

    @pytest.mark.asyncio
    async def test_sent_at_is_none_until_sent_then_set(self):
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
        assert notification.sent_at is None

        await self.notification_service.send(notification)

        sent_notification = await self.notification_service.get_notification(notification.id)
        assert sent_notification.sent_at is not None

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
    async def test_in_app_list_and_count_require_in_app_adapter(self):
        with pytest.raises(NotificationError):
            await self.notification_service.get_in_app_notifications(user_id=1)
        with pytest.raises(NotificationError):
            await self.notification_service.get_in_app_notifications_count(user_id=1)
        with pytest.raises(NotificationError):
            await self.notification_service.get_in_app_unread_count(user_id=1)

    def _make_in_app(self, user_id, status):
        return Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="body.html",
            context_name="test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=None,
            subject_template="subject.txt",
            preheader_template="preheader.html",
            status=status,
        )

    @pytest.mark.asyncio
    async def test_async_backend_in_app_list_count_and_bulk(self):
        backend = FakeAsyncIOFileBackend(database_file_name="async-in-app-tests.json")
        backend.notifications = [
            self._make_in_app(1, NotificationStatus.SENT.value),
            self._make_in_app(1, NotificationStatus.READ.value),
            self._make_in_app(1, NotificationStatus.SENT.value),
            self._make_in_app(2, NotificationStatus.SENT.value),
            self._make_in_app(1, NotificationStatus.PENDING_SEND.value),
        ]
        try:
            # all (read + unread), excludes PENDING_SEND, for user 1 -> 3
            all_for_user = list(await backend.filter_all_in_app_notifications(1))
            assert len(all_for_user) == 3
            assert await backend.count_in_app_notifications(1) == 3
            assert await backend.count_in_app_unread_notifications(1) == 2

            page = list(await backend.filter_in_app_notifications(1, page=1, page_size=2))
            assert len(page) == 2

            sent_ids = [
                n.id
                for n in backend.notifications
                if n.user_id == 1 and n.status == NotificationStatus.SENT.value
            ]
            result = list(await backend.mark_sent_as_read_bulk(sent_ids, user_id=1))
            assert {n.id for n in result} == set(sent_ids)
            assert all(n.status == NotificationStatus.READ.value for n in result)
            assert await backend.count_in_app_unread_notifications(1) == 0
            # idempotent re-run
            again = list(await backend.mark_sent_as_read_bulk(sent_ids, user_id=1))
            assert {n.id for n in again} == set(sent_ids)
        finally:
            await backend.clear()

    @pytest.mark.asyncio
    async def test_async_backend_mark_sent_as_read_bulk_sets_read_at_on_affected_rows_only(self):
        backend = FakeAsyncIOFileBackend(database_file_name="async-in-app-tests.json")
        already_read = self._make_in_app(1, NotificationStatus.READ.value)
        already_read.read_at = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        newly_read = self._make_in_app(1, NotificationStatus.SENT.value)
        skipped = self._make_in_app(1, NotificationStatus.SENT.value)
        backend.notifications = [already_read, newly_read, skipped]
        try:
            result = list(
                await backend.mark_sent_as_read_bulk([already_read.id, newly_read.id], user_id=1)
            )

            assert {n.id for n in result} == {already_read.id, newly_read.id}
            updated_newly_read = await backend.get_notification(newly_read.id)
            assert updated_newly_read.read_at is not None
            # Already-read rows are left untouched.
            updated_already_read = await backend.get_notification(already_read.id)
            assert updated_already_read.read_at == already_read.read_at
            # Notifications not passed to mark_sent_as_read_bulk are never touched.
            updated_skipped = await backend.get_notification(skipped.id)
            assert updated_skipped.read_at is None
        finally:
            await backend.clear()

    @pytest.mark.asyncio
    @patch(
        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter.send"
    )
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

        pending_notifications_before = (
            await self.notification_service.get_all_future_notifications()
        )
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

        pending_notifications = await self.notification_service.get_future_notifications(
            page=1, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification1.id

        pending_notifications = await self.notification_service.get_future_notifications(
            page=2, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification2.id

        pending_notifications = await self.notification_service.get_future_notifications(
            page=3, page_size=1
        )
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

        pending_notifications = (
            await self.notification_service.get_all_future_notifications_from_user(user_id=1)
        )
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
        assert next(iter(pending_notifications)).id == notification1.id

        pending_notifications = await self.notification_service.get_future_notifications_from_user(
            user_id=1, page=2, page_size=1
        )
        assert len(list(pending_notifications)) == 1
        assert next(iter(pending_notifications)).id == notification2.id

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
        notification_backend = FakeAsyncIOFileBackend(
            database_file_name="service-tests-notifications.json"
        )
        notification_adapters = [
            FakeAsyncIOEmailAdapter(
                backend=notification_backend, template_renderer=FakeTemplateRenderer()
            ),
        ]
        service = AsyncIONotificationService(
            notification_adapters=notification_adapters,
            notification_backend=notification_backend,
        )

        assert service.notification_backend == notification_backend
        assert service.notification_adapters == notification_adapters

    async def test_instances_config_initializes_notification_settings_singleton(self):
        """Config must reach NotificationSettings even when backend/adapters are instances.

        Neither get_asyncio_notification_backend nor AsyncIOBaseNotificationAdapter.__init__
        runs on that path, so AsyncIONotificationService.__init__ has to construct
        NotificationSettings(config) itself, or the config argument is silently dropped and
        the app falls back to framework defaults.
        """
        _reset_notification_settings_singleton(self)

        class _FakeConfig:
            NOTIFICATION_DEFAULT_FROM_EMAIL = "async-singleton-test@example.com"

        notification_backend = FakeAsyncIOFileBackend(
            database_file_name="service-tests-notifications.json"
        )
        notification_adapters = [
            FakeAsyncIOEmailAdapter(
                backend=notification_backend, template_renderer=FakeTemplateRenderer()
            ),
        ]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            AsyncIONotificationService(
                notification_adapters=notification_adapters,
                notification_backend=notification_backend,
                config=_FakeConfig(),
            )

        assert (
            NotificationSettings().NOTIFICATION_DEFAULT_FROM_EMAIL
            == "async-singleton-test@example.com"
        )

        # First construction wins: a later call with a different config is ignored, because
        # the singleton is already built and SingletonMeta returns the cached instance
        # without re-running __init__.
        NotificationSettings(object())
        assert (
            NotificationSettings().NOTIFICATION_DEFAULT_FROM_EMAIL
            == "async-singleton-test@example.com"
        )

    async def test_instanciate_with_adapter_kwargs_tuple_form(self):
        """The sync service accepts an (import_str, kwargs) tuple as an adapter's first
        element, and get_asyncio_notification_adapters already handles that shape too. The
        async service's own construction guard must accept it as well.
        """
        service = AsyncIONotificationService(
            notification_adapters=[
                (
                    (
                        "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                        {"extra_config": "value"},
                    ),
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
            notification_backend="vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
            notification_backend_kwargs={"database_file_name": "service-tests-notifications.json"},
        )

        adapters = list(service.notification_adapters)
        assert len(adapters) == 1
        assert adapters[0].adapter_kwargs == {"extra_config": "value"}

    async def test_constructor_rejects_duplicate_adapter_notification_types(self):
        """Test that constructing with duplicate adapter types raises DuplicateNotificationAdapterError."""
        _reset_notification_settings_singleton(self)
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        adapter1 = FakeAsyncIOEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )
        adapter2 = FakeAsyncIOEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )

        with pytest.raises(DuplicateNotificationAdapterError) as exc_info:
            AsyncIONotificationService(
                notification_adapters=[adapter1, adapter2],
                notification_backend=backend,
            )

        error_message = str(exc_info.value)
        assert "Duplicate adapter notification types are not allowed" in error_message
        assert "EMAIL" in error_message
        # adapter1 and adapter2 are the same class, so they share one import string. Assert
        # it appears twice, once per adapter, so this test still fails if the formatter only
        # names the first adapter.
        assert error_message.count(adapter1.adapter_import_str) == 2

    async def test_constructor_rejects_duplicate_adapter_notification_types_from_import_strings(
        self,
    ):
        """Test that duplicates arriving as (import_str, renderer_str) tuples are also rejected."""
        _reset_notification_settings_singleton(self)
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        adapter_import_str = (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter"
        )

        with pytest.raises(DuplicateNotificationAdapterError) as exc_info:
            AsyncIONotificationService(
                notification_adapters=[
                    (
                        adapter_import_str,
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    ),
                    (
                        adapter_import_str,
                        "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                    ),
                ],
                notification_backend=backend,
            )

        error_message = str(exc_info.value)
        assert "Duplicate adapter notification types are not allowed" in error_message
        assert "EMAIL" in error_message
        assert error_message.count(adapter_import_str) == 2

    async def test_constructor_allows_single_adapter(self):
        """Test that constructing with a single adapter works fine."""
        _reset_notification_settings_singleton(self)
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")
        adapter = FakeAsyncIOEmailAdapter(
            template_renderer=FakeTemplateRenderer(),
            backend=backend,
        )

        service = AsyncIONotificationService(
            notification_adapters=[adapter],
            notification_backend=backend,
        )
        assert [a.notification_type for a in service.notification_adapters] == [
            NotificationTypes.EMAIL
        ]

    async def test_constructor_allows_different_adapter_types(self):
        """Test that constructing with adapters of different types works fine."""
        _reset_notification_settings_singleton(self)
        backend = FakeAsyncIOFileBackend(database_file_name="service-tests-notifications.json")

        service = AsyncIONotificationService(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeAsyncIOInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                ),
            ],
            notification_backend=backend,
        )
        assert [a.notification_type for a in service.notification_adapters] == [
            NotificationTypes.EMAIL,
            NotificationTypes.IN_APP,
        ]


class NotificationServiceImportTestCase(TestCase):
    def test_importing_module_does_not_import_requests(self):
        """Importing notification_service must not pull in requests as a side effect.

        requests is only needed by service_utils.download_from_url, which imports it lazily
        at call time, so a bare import of the service module must not add it to sys.modules.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import vintasend.services.notification_service, sys; "
                "assert 'requests' not in sys.modules",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


# Methods that intentionally exist on only one of NotificationService / AsyncIONotificationService.
# Per AGENTS.md's sync/AsyncIO parity rule, any other one-sided method is a drift bug, not a
# design choice, and should fail the test below instead of growing this list. Each entry names
# the one class that carries the method and why the asymmetry is deliberate, so a method that
# moves to the other class -- rather than simply appearing on a second class -- still fails.
SERVICE_METHOD_PARITY_ALLOWLIST: dict[str, tuple[str, str]] = {
    "delayed_send": (
        "NotificationService",
        "Sends through an AsyncBaseNotificationAdapter, a sync adapter that hands delivery to "
        "a task queue (see AGENTS.md's note on notification_adapters/async_base.py) rather than "
        "genuine async/await. AsyncIONotificationService has no such adapter kind to send "
        "through today; the background-send-queue plan adds one.",
    ),
    "_send_notification_with_error_logging": (
        "AsyncIONotificationService",
        "Its send_pending_notifications sends notifications concurrently with asyncio.gather, "
        "which needs a per-notification coroutine to carry the try/except/logging for each one. "
        "The sync twin runs the same branches inline in a plain for loop and never needed a "
        "separate helper for it.",
    ),
}


class ServiceMethodParityTestCase(TestCase):
    """
    Guards the sync/AsyncIO parity rule from AGENTS.md: NotificationService and
    AsyncIONotificationService must expose the same set of methods, except for the
    differences named in SERVICE_METHOD_PARITY_ALLOWLIST above.

    "Method set" here covers every function, staticmethod, classmethod, and property defined
    directly on the class body, including `_`-prefixed helpers and dunders such as __init__,
    not just the public, non-underscore API. Most of the drift this plan exists to close lives
    in the `_`-prefixed helpers, so a check limited to public names would miss it.
    """

    def _method_names(self, cls: type) -> set[str]:
        return {
            name
            for name, value in vars(cls).items()
            if inspect.isfunction(value) or isinstance(value, (staticmethod, classmethod, property))
        }

    def test_method_sets_match_except_for_allowlisted_differences(self):
        sync_names = self._method_names(NotificationService)
        async_names = self._method_names(AsyncIONotificationService)

        sync_only = sync_names - async_names
        async_only = async_names - sync_names

        unexplained_sync_only = sync_only - SERVICE_METHOD_PARITY_ALLOWLIST.keys()
        unexplained_async_only = async_only - SERVICE_METHOD_PARITY_ALLOWLIST.keys()

        assert not unexplained_sync_only, (
            "NotificationService defines methods AsyncIONotificationService lacks, with no "
            f"allowlist entry explaining why: {sorted(unexplained_sync_only)}. Add a matching "
            "method to AsyncIONotificationService, or a reasoned entry to "
            "SERVICE_METHOD_PARITY_ALLOWLIST."
        )
        assert not unexplained_async_only, (
            "AsyncIONotificationService defines methods NotificationService lacks, with no "
            f"allowlist entry explaining why: {sorted(unexplained_async_only)}. Add a matching "
            "method to NotificationService, or a reasoned entry to "
            "SERVICE_METHOD_PARITY_ALLOWLIST."
        )

        # sync_only and async_only are disjoint by construction (each is one side minus the
        # other), so checking membership in the named owner's set alone catches a method that
        # moved to the other class, not just one that vanished from both.
        owner_by_class_name = {
            "NotificationService": sync_only,
            "AsyncIONotificationService": async_only,
        }
        for method_name, (owner_class_name, _reason) in SERVICE_METHOD_PARITY_ALLOWLIST.items():
            assert method_name in owner_by_class_name[owner_class_name], (
                f"SERVICE_METHOD_PARITY_ALLOWLIST says {method_name!r} belongs only to "
                f"{owner_class_name}, but it is not one-sided in {owner_class_name}'s favor "
                "any more -- it may have moved to the other class, or stopped being one-sided "
                "at all. Update the allowlist entry to match reality."
            )

        # An allowlist entry that no longer names a real asymmetry means the drift it
        # described has already been fixed elsewhere, so flag it rather than let it go stale.
        stale_entries = set(SERVICE_METHOD_PARITY_ALLOWLIST.keys()) - (sync_only | async_only)
        assert not stale_entries, (
            f"SERVICE_METHOD_PARITY_ALLOWLIST names methods that are no longer asymmetric: "
            f"{sorted(stale_entries)}. Remove the stale entries."
        )
