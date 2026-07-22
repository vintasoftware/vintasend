"""Integration tests for the worker entrypoint: factory resolution, caching, and round trips."""

import io
import os
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pytest

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationServiceFactoryError
from vintasend.services.dataclasses import NotificationAttachment, NotificationContextDict
from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncEmailAdapter,
    FakeAsyncIOBackgroundEmailAdapter,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_queue_services.stubs.fake_queue_service import (
    FakeAsyncIOQueueService,
    FakeQueueService,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeTemplateRenderer,
)
from vintasend.tasks import background_tasks
from vintasend.tasks.background_tasks import (
    _reset_notification_service_cache,
    async_send_notification,
    get_notification_service,
    send_notification,
)
from vintasend.tests.utils import _reset_notification_settings_singleton


DATABASE_FILE_NAME = "background-tasks-tests-notifications.json"
FACTORY_IMPORT_STR = "vintasend.tests.test_tasks.test_background_tasks.build_notification_service"


class FactoryState:
    """Stands in for whatever a host's factory closes over -- a session, an engine, a broker.

    The backend lives here rather than being rebuilt from settings so the worker's service
    shares the web process's storage, which is what a real backend gets for free from its
    database and what the in-memory fake needs to be handed explicitly.
    """

    def __init__(self) -> None:
        self.backend: FakeFileBackend | None = None
        self.calls = 0

    def reset(self) -> None:
        self.backend = None
        self.calls = 0


FACTORY_STATE = FactoryState()


@register_context("background_task_context")
def background_task_context() -> NotificationContextDict:
    return NotificationContextDict({"test": "test"})


def build_notification_service() -> NotificationService:
    """A host's NOTIFICATION_SERVICE_FACTORY: builds the worker's service once per process."""
    FACTORY_STATE.calls += 1
    return NotificationService(
        notification_adapters=[
            FakeAsyncEmailAdapter(
                template_renderer=FakeTemplateRenderer(), backend=FACTORY_STATE.backend
            )
        ],
        notification_backend=FACTORY_STATE.backend,
    )


def failing_factory() -> NotificationService:
    raise RuntimeError("the host's factory blew up")


def factory_returning_a_non_service() -> Any:
    return "not a notification service"


NOT_A_CALLABLE = object()


class BackgroundTasksTestCase(TestCase):
    def setUp(self):
        self.addCleanup(_reset_notification_service_cache)
        self.addCleanup(FACTORY_STATE.reset)
        self.addCleanup(FakeFileBackend(database_file_name=DATABASE_FILE_NAME).clear)
        _reset_notification_service_cache()
        FACTORY_STATE.reset()
        FACTORY_STATE.backend = FakeFileBackend(database_file_name=DATABASE_FILE_NAME)
        # NotificationSettings reads its config on first construction only, and the first
        # construction happens the moment any service is built. So the setting has to be in
        # place -- and the singleton cleared -- before the test builds anything.
        self.set_factory_setting(FACTORY_IMPORT_STR)

    def set_factory_setting(self, value: str | None) -> None:
        """Point NOTIFICATION_SERVICE_FACTORY at `value`, or unset it, for this test.

        `get_config` only consults environment variables once a framework is detected --
        with no framework it returns `{}` for everything -- so the framework probe is
        stubbed for the duration of the test. None of the three frameworks is a dependency
        of this package, so there is no real one to detect here.
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

    def _web_process_service(self, queue_service: FakeQueueService) -> NotificationService:
        """The service the web process would hold: same backend, plus a queue service."""
        return NotificationService(
            notification_adapters=[
                FakeAsyncEmailAdapter(
                    template_renderer=FakeTemplateRenderer(), backend=FACTORY_STATE.backend
                )
            ],
            notification_backend=FACTORY_STATE.backend,
            notification_queue_service=queue_service,
        )

    def _create_notification(self, service: NotificationService, **kwargs):
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="background_task_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            **kwargs,
        )

    def test_get_notification_service_resolves_the_configured_factory(self):
        service = get_notification_service()

        assert isinstance(service, NotificationService)
        assert service.notification_backend is FACTORY_STATE.backend
        assert FACTORY_STATE.calls == 1

    def test_get_notification_service_caches_the_service_per_process(self):
        first = get_notification_service()
        second = get_notification_service()

        assert first is second
        assert FACTORY_STATE.calls == 1

    def test_send_notification_calls_the_factory_only_once_across_tasks(self):
        queue_service = FakeQueueService()
        web_service = self._web_process_service(queue_service)
        first = self._create_notification(web_service)
        second = self._create_notification(web_service)

        send_notification(first.id)
        send_notification(second.id)

        assert FACTORY_STATE.calls == 1

    def test_id_only_round_trip_delivers_and_marks_sent(self):
        queue_service = FakeQueueService()
        web_service = self._web_process_service(queue_service)

        notification = self._create_notification(web_service)

        # The web process put nothing but the id on the queue.
        assert queue_service.enqueued_notification_ids == [notification.id]
        assert (
            web_service.get_notification(notification.id).status
            == NotificationStatus.PENDING_SEND.value
        )

        send_notification(queue_service.enqueued_notification_ids[0])

        worker_service = background_tasks._cached_notification_service
        assert worker_service is not None
        adapter = next(iter(worker_service.notification_adapters))
        assert len(adapter.sent_emails) == 1
        delivered, context, _attachments = adapter.sent_emails[0]
        assert str(delivered.id) == str(notification.id)
        assert context == {"test": "test"}

        stored = web_service.get_notification(notification.id)
        assert stored.status == NotificationStatus.SENT.value
        assert stored.context_used == {"test": "test"}

    def test_round_trip_delivers_attachments(self):
        """Attachments survive the background path now that only the id crosses the queue.

        Under the old payload the notification was serialized into the task, so file handles
        could not travel and the worker received a placeholder. The worker now reads the real
        stored attachment back from the backend.
        """
        queue_service = FakeQueueService()
        web_service = self._web_process_service(queue_service)

        notification = self._create_notification(
            web_service,
            attachments=[
                NotificationAttachment(
                    file=io.BytesIO(b"attachment payload"),
                    filename="report.txt",
                    content_type="text/plain",
                    description="a real file, not a placeholder",
                )
            ],
        )

        send_notification(queue_service.enqueued_notification_ids[0])

        worker_service = background_tasks._cached_notification_service
        assert worker_service is not None
        adapter = next(iter(worker_service.notification_adapters))
        delivered, _context, attachment_info = adapter.sent_emails[0]

        assert [info["filename"] for info in attachment_info] == ["report.txt"]
        assert attachment_info[0]["size"] == len(b"attachment payload")
        assert delivered.attachments[0].file.read() == b"attachment payload"
        assert web_service.get_notification(notification.id).status == NotificationStatus.SENT.value

    def test_send_notification_accepts_an_explicit_service_override(self):
        queue_service = FakeQueueService()
        web_service = self._web_process_service(queue_service)
        notification = self._create_notification(web_service)

        # The override wins: the factory is never consulted.
        send_notification(notification.id, notification_service=web_service)

        assert FACTORY_STATE.calls == 0
        assert web_service.get_notification(notification.id).status == NotificationStatus.SENT.value

    def test_send_notification_swallows_and_logs_a_poisoned_task(self):
        """A task that cannot be handled must not take the worker down with it."""
        with patch("vintasend.tasks.background_tasks.logger") as mocked_logger:
            send_notification("an-id-that-does-not-exist", notification_service=None)

        mocked_logger.exception.assert_called_once()

    def test_get_notification_service_without_the_setting(self):
        self.set_factory_setting(None)

        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

    def test_get_notification_service_with_an_unimportable_factory(self):
        self.set_factory_setting("vintasend.does.not.exist.factory")

        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

    def test_get_notification_service_with_a_non_callable_factory(self):
        self.set_factory_setting("vintasend.tests.test_tasks.test_background_tasks.NOT_A_CALLABLE")

        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

    def test_get_notification_service_when_the_factory_raises(self):
        self.set_factory_setting("vintasend.tests.test_tasks.test_background_tasks.failing_factory")

        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

    def test_get_notification_service_when_the_factory_returns_a_non_service(self):
        self.set_factory_setting(
            "vintasend.tests.test_tasks.test_background_tasks.factory_returning_a_non_service"
        )

        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

    def test_a_failed_factory_is_not_cached(self):
        self.set_factory_setting("vintasend.tests.test_tasks.test_background_tasks.failing_factory")
        with pytest.raises(NotificationServiceFactoryError):
            get_notification_service()

        self.set_factory_setting(FACTORY_IMPORT_STR)
        service = get_notification_service()

        assert isinstance(service, NotificationService)

    def test_send_notification_logs_and_does_not_hang_on_an_asyncio_service(self):
        """A sync host wired to an AsyncIO factory must not silently no-op or block."""
        self.set_factory_setting(
            "vintasend.tests.test_tasks.test_background_tasks.build_async_notification_service"
        )
        self.addCleanup(ASYNC_FACTORY_STATE.reset)
        ASYNC_FACTORY_STATE.backend = FakeAsyncIOFileBackend(
            database_file_name=ASYNCIO_DATABASE_FILE_NAME
        )

        with patch("vintasend.tasks.background_tasks.logger") as mocked_logger:
            send_notification("some-id")

        mocked_logger.exception.assert_called_once()


class ASyncFactoryState:
    """Stands in for whatever an AsyncIO host's factory closes over."""

    def __init__(self) -> None:
        self.backend: FakeAsyncIOFileBackend | None = None
        self.calls = 0

    def reset(self) -> None:
        self.backend = None
        self.calls = 0


ASYNC_FACTORY_STATE = ASyncFactoryState()
ASYNCIO_DATABASE_FILE_NAME = "background-tasks-tests-notifications-asyncio.json"
ASYNC_FACTORY_IMPORT_STR = (
    "vintasend.tests.test_tasks.test_background_tasks.build_async_notification_service"
)


def build_async_notification_service() -> AsyncIONotificationService:
    """An AsyncIO host's NOTIFICATION_SERVICE_FACTORY: built once per process."""
    ASYNC_FACTORY_STATE.calls += 1
    return AsyncIONotificationService(
        notification_adapters=[
            FakeAsyncIOBackgroundEmailAdapter(
                template_renderer=FakeTemplateRenderer(), backend=ASYNC_FACTORY_STATE.backend
            )
        ],
        notification_backend=ASYNC_FACTORY_STATE.backend,
    )


class AsyncIOBackgroundTasksTestCase(IsolatedAsyncioTestCase):
    """Mirrors BackgroundTasksTestCase's round-trip coverage for the AsyncIO entrypoint."""

    def setUp(self):
        self.addCleanup(_reset_notification_service_cache)
        self.addCleanup(ASYNC_FACTORY_STATE.reset)
        _reset_notification_service_cache()
        ASYNC_FACTORY_STATE.reset()
        ASYNC_FACTORY_STATE.backend = FakeAsyncIOFileBackend(
            database_file_name=ASYNCIO_DATABASE_FILE_NAME
        )
        self.set_factory_setting(ASYNC_FACTORY_IMPORT_STR)

    async def asyncTearDown(self):
        await FakeAsyncIOFileBackend(database_file_name=ASYNCIO_DATABASE_FILE_NAME).clear()

    def set_factory_setting(self, value: str | None) -> None:
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

    def _web_process_service(
        self, queue_service: FakeAsyncIOQueueService
    ) -> AsyncIONotificationService:
        """The service the web process would hold: same backend, plus a queue service."""
        return AsyncIONotificationService(
            notification_adapters=[
                FakeAsyncIOBackgroundEmailAdapter(
                    template_renderer=FakeTemplateRenderer(), backend=ASYNC_FACTORY_STATE.backend
                )
            ],
            notification_backend=ASYNC_FACTORY_STATE.backend,
            notification_queue_service=queue_service,
        )

    async def _create_notification(self, service: AsyncIONotificationService, **kwargs):
        return await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="background_task_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            **kwargs,
        )

    async def test_get_notification_service_resolves_the_configured_factory(self):
        service = get_notification_service()

        assert isinstance(service, AsyncIONotificationService)
        assert service.notification_backend is ASYNC_FACTORY_STATE.backend
        assert ASYNC_FACTORY_STATE.calls == 1

    async def test_id_only_round_trip_delivers_and_marks_sent(self):
        queue_service = FakeAsyncIOQueueService()
        web_service = self._web_process_service(queue_service)

        notification = await self._create_notification(web_service)

        # The web process put nothing but the id on the queue.
        assert queue_service.enqueued_notification_ids == [notification.id]
        assert (
            await web_service.get_notification(notification.id)
        ).status == NotificationStatus.PENDING_SEND.value

        await async_send_notification(queue_service.enqueued_notification_ids[0])

        worker_service = background_tasks._cached_notification_service
        assert worker_service is not None
        adapter = next(iter(worker_service.notification_adapters))
        assert len(adapter.sent_emails) == 1
        delivered, context, _attachments = adapter.sent_emails[0]
        assert str(delivered.id) == str(notification.id)
        assert context == {"test": "test"}

        stored = await web_service.get_notification(notification.id)
        assert stored.status == NotificationStatus.SENT.value
        assert stored.context_used == {"test": "test"}

    async def test_round_trip_delivers_attachments(self):
        """Attachments survive the background path the same way they do for the sync worker."""
        queue_service = FakeAsyncIOQueueService()
        web_service = self._web_process_service(queue_service)

        notification = await self._create_notification(
            web_service,
            attachments=[
                NotificationAttachment(
                    file=io.BytesIO(b"attachment payload"),
                    filename="report.txt",
                    content_type="text/plain",
                    description="a real file, not a placeholder",
                )
            ],
        )

        await async_send_notification(queue_service.enqueued_notification_ids[0])

        worker_service = background_tasks._cached_notification_service
        assert worker_service is not None
        adapter = next(iter(worker_service.notification_adapters))
        delivered, _context, attachment_info = adapter.sent_emails[0]

        assert [info["filename"] for info in attachment_info] == ["report.txt"]
        assert attachment_info[0]["size"] == len(b"attachment payload")
        assert delivered.attachments[0].file.read() == b"attachment payload"
        assert (
            await web_service.get_notification(notification.id)
        ).status == NotificationStatus.SENT.value

    async def test_async_send_notification_accepts_an_explicit_service_override(self):
        queue_service = FakeAsyncIOQueueService()
        web_service = self._web_process_service(queue_service)
        notification = await self._create_notification(web_service)

        # The override wins: the factory is never consulted.
        await async_send_notification(notification.id, notification_service=web_service)

        assert ASYNC_FACTORY_STATE.calls == 0
        assert (
            await web_service.get_notification(notification.id)
        ).status == NotificationStatus.SENT.value

    async def test_async_send_notification_swallows_and_logs_a_poisoned_task(self):
        """A task that cannot be handled must not take the worker down with it."""
        with patch("vintasend.tasks.background_tasks.logger") as mocked_logger:
            await async_send_notification("an-id-that-does-not-exist", notification_service=None)

        mocked_logger.exception.assert_called_once()

    async def test_async_send_notification_logs_when_the_resolved_service_is_sync(self):
        """An AsyncIO host wired to a sync factory must not silently no-op."""
        self.set_factory_setting(FACTORY_IMPORT_STR)
        self.addCleanup(FACTORY_STATE.reset)
        FACTORY_STATE.backend = FakeFileBackend(database_file_name=DATABASE_FILE_NAME)
        self.addCleanup(FakeFileBackend(database_file_name=DATABASE_FILE_NAME).clear)

        with patch("vintasend.tasks.background_tasks.logger") as mocked_logger:
            await async_send_notification("some-id")

        mocked_logger.exception.assert_called_once()
