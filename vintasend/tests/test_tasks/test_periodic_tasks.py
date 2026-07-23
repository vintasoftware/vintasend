"""Tests for the periodic drain entrypoint, which builds its service from the host factory."""

import datetime
import os
from unittest import TestCase
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationServiceFactoryError
from vintasend.services.dataclasses import NotificationContextDict
from vintasend.services.notification_adapters.stubs.fake_in_app_adapter import FakeInAppAdapter
from vintasend.services.notification_backends.stubs.fake_backend import FakeFileBackend
from vintasend.services.notification_service import NotificationService, register_context
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeTemplateRenderer,
)
from vintasend.tasks.background_tasks import _reset_notification_service_cache
from vintasend.tasks.periodic_tasks import periodic_send_pending_notifications
from vintasend.tests.utils import _reset_notification_settings_singleton


DATABASE_FILE_NAME = "periodic-tests-notifications.json"
FACTORY_IMPORT_STR = "vintasend.tests.test_tasks.test_periodic_tasks.build_notification_service"


class FactoryState:
    """Holds what a host's factory would close over, and counts how often it is called."""

    def __init__(self) -> None:
        self.backend: FakeFileBackend | None = None
        self.calls = 0

    def reset(self) -> None:
        self.backend = None
        self.calls = 0


FACTORY_STATE = FactoryState()


@register_context("periodic_task_context")
def periodic_task_context() -> NotificationContextDict:
    return NotificationContextDict({"test": "test"})


def build_notification_service() -> NotificationService:
    """A host's NOTIFICATION_SERVICE_FACTORY, as the periodic task resolves it."""
    FACTORY_STATE.calls += 1
    return NotificationService(
        notification_adapters=[
            FakeInAppAdapter(
                template_renderer=FakeTemplateRenderer(), backend=FACTORY_STATE.backend
            )
        ],
        notification_backend=FACTORY_STATE.backend,
    )


class PeriodicSendPendingNotificationsTestCase(TestCase):
    """
    The periodic task used to take adapter/backend import strings and kwargs, and scan the
    adapters for `restore_backend_kwargs` / `restore_config` hooks to undo the serialization
    the old queue payload needed. With only a notification id on the queue there is nothing
    to restore, and the service comes from the host's factory instead.
    """

    def setUp(self):
        self.addCleanup(_reset_notification_service_cache)
        self.addCleanup(FACTORY_STATE.reset)
        self.addCleanup(FakeFileBackend(database_file_name=DATABASE_FILE_NAME).clear)
        _reset_notification_service_cache()
        FACTORY_STATE.reset()
        FACTORY_STATE.backend = FakeFileBackend(database_file_name=DATABASE_FILE_NAME)
        self.set_factory_setting(FACTORY_IMPORT_STR)

    def set_factory_setting(self, value: str | None) -> None:
        """Point NOTIFICATION_SERVICE_FACTORY at `value`, or unset it, for this test.

        `get_config` only consults environment variables once a framework is detected, so
        the framework probe is stubbed -- none of the three is a dependency of this package.
        """
        framework_patcher = patch("vintasend.app_settings.detect_framework", return_value="FastAPI")
        framework_patcher.start()
        self.addCleanup(framework_patcher.stop)

        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        if value is None:
            os.environ.pop("NOTIFICATION_SERVICE_FACTORY", None)
        else:
            os.environ["NOTIFICATION_SERVICE_FACTORY"] = value

        _reset_notification_settings_singleton(self)
        _reset_notification_service_cache()

    def _create_pending_notification(self, service: NotificationService):
        send_after = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="periodic_task_context",
            context_kwargs=NotificationContextDict({}),
            send_after=send_after,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

    def test_builds_the_service_from_the_factory_and_sends_pending_notifications(self):
        service = build_notification_service()
        notification = self._create_pending_notification(service)
        assert notification.send_after is not None
        assert (
            service.get_notification(notification.id).status
            == NotificationStatus.PENDING_SEND.value
        )
        calls_before = FACTORY_STATE.calls

        with freeze_time(notification.send_after + datetime.timedelta(days=1)):
            periodic_send_pending_notifications()

        assert FACTORY_STATE.calls == calls_before + 1
        assert service.get_notification(notification.id).status == NotificationStatus.SENT.value

    def test_reuses_the_cached_service_across_runs(self):
        periodic_send_pending_notifications()
        periodic_send_pending_notifications()

        assert FACTORY_STATE.calls == 1

    def test_accepts_an_explicit_service_override(self):
        service = build_notification_service()
        notification = self._create_pending_notification(service)
        assert notification.send_after is not None
        calls_before = FACTORY_STATE.calls

        with freeze_time(notification.send_after + datetime.timedelta(days=1)):
            periodic_send_pending_notifications(notification_service=service)

        assert FACTORY_STATE.calls == calls_before
        assert service.get_notification(notification.id).status == NotificationStatus.SENT.value

    def test_raises_when_no_factory_is_configured(self):
        self.set_factory_setting(None)

        with pytest.raises(NotificationServiceFactoryError):
            periodic_send_pending_notifications()
