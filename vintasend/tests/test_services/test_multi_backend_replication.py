import datetime
import os
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import (
    BackendNotFoundError,
    NotificationError,
    NotificationNotFoundError,
    ReplicationError,
)
from vintasend.services.dataclasses import NotificationContextDict
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_queue_services.asyncio_replication_base import (
    AsyncIOBaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.replication_base import (
    BaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.stubs.fake_replication_queue_service import (
    FakeAsyncIOReplicationQueueService,
    FakeReplicationQueueService,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.tests.test_services.test_multi_backend_writes import (
    AsyncIONoSnapshotApplyReplica,
    NoSnapshotApplyReplica,
)
from vintasend.tests.utils import _reset_notification_settings_singleton


IN_APP_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
)

ASYNCIO_IN_APP_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeAsyncIOInAppAdapter",
    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
)


def _build_context(test):
    if test != "test":
        raise ValueError()
    return NotificationContextDict({"test": "test"})


def _future() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=1)


class _RaisingForBackendQueueService(FakeReplicationQueueService):
    """A queue service that raises when asked to enqueue for one specific backend.

    Records every other enqueue as usual, so a test can assert which backends were enqueued
    and which fell back to inline replication.
    """

    def __init__(self, failing_backend_identifier: str) -> None:
        super().__init__()
        self.failing_backend_identifier = failing_backend_identifier

    def enqueue_replication(self, notification_id, backend_identifier) -> None:
        if backend_identifier == self.failing_backend_identifier:
            raise RuntimeError("broker is down for this backend")
        super().enqueue_replication(notification_id, backend_identifier)


class _AsyncIORaisingForBackendQueueService(FakeAsyncIOReplicationQueueService):
    def __init__(self, failing_backend_identifier: str) -> None:
        super().__init__()
        self.failing_backend_identifier = failing_backend_identifier

    async def enqueue_replication(self, notification_id, backend_identifier) -> None:
        if backend_identifier == self.failing_backend_identifier:
            raise RuntimeError("broker is down for this backend")
        await super().enqueue_replication(notification_id, backend_identifier)


class QueuedReplicationTestCase(TestCase):
    """Sync ``NotificationService`` queued replication + fallback (Phase 3)."""

    def setUp(self):
        register_context("multi_backend_replication_test_context")(_build_context)
        self.primary_backend = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_one = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_two = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.queue = FakeReplicationQueueService()
        self._owned_backends = [self.primary_backend, self.replica_one, self.replica_two]

    def tearDown(self):
        for backend in self._owned_backends:
            backend.clear()

    def build_service(self, **kwargs) -> NotificationService:
        kwargs.setdefault("notification_adapters", [IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return NotificationService(**kwargs)

    def _create(self, service: NotificationService, send_after=None, title="Notification"):
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_replication_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    def test_queued_mode_enqueues_one_task_per_additional_backend(self):
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )

        # A scheduled create is a single primary write, so exactly one task per replica.
        notification = self._create(service, send_after=_future())

        assert self.queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-2"),
        ]
        # Nothing replicated inline: the worker has not run.
        with self.assertRaises(NotificationNotFoundError):
            self.replica_one.get_notification(notification.id)
        with self.assertRaises(NotificationNotFoundError):
            self.replica_two.get_notification(notification.id)

    def test_missing_id_falls_back_to_inline_and_enqueues_nothing(self):
        # An inline service seeds both backends so the bulk mark has rows to converge.
        inline_service = self.build_service(additional_backends=[self.replica_one])
        notifications = [self._create(inline_service, title=f"n{i}") for i in range(2)]
        ids = [n.id for n in notifications]

        queued_service = self.build_service(
            additional_backends=[self.replica_one],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            queued_service.mark_read_bulk(ids, user_id=1)

        # A bulk mark carries no single notification id, so queued mode cannot enqueue it and
        # falls back to inline for the whole write.
        assert self.queue.enqueued_replications == []
        assert any("falling back to inline replication" in message for message in captured.output)
        for notification_id in ids:
            assert self.replica_one.get_notification(notification_id).status == (
                NotificationStatus.READ.value
            )

    def test_enqueue_error_inline_replicates_only_the_failed_backend(self):
        queue = _RaisingForBackendQueueService(failing_backend_identifier="backend-2")
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=queue,
        )

        notification = self._create(service, send_after=_future())

        # backend-1 enqueued cleanly; backend-2's enqueue raised, so it was replicated inline.
        assert queue.enqueued_replications == [(notification.id, "backend-1")]
        with self.assertRaises(NotificationNotFoundError):
            self.replica_one.get_notification(notification.id)
        assert self.replica_two.get_notification(notification.id).id == notification.id

    def test_enqueue_error_on_middle_backend_still_enqueues_the_one_after_it(self):
        replica_three = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(replica_three)
        queue = _RaisingForBackendQueueService(failing_backend_identifier="backend-2")
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two, replica_three],
            replication_mode="queued",
            replication_queue_service=queue,
        )

        notification = self._create(service, send_after=_future())

        # backend-1 and backend-3 enqueued cleanly; backend-2's enqueue raised (and was
        # replicated inline instead), proving a failing middle backend does not stop the loop
        # from reaching the backend after it.
        assert queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-3"),
        ]
        with self.assertRaises(NotificationNotFoundError):
            self.replica_one.get_notification(notification.id)
        assert self.replica_two.get_notification(notification.id).id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            replica_three.get_notification(notification.id)

    def test_no_queue_service_falls_back_to_inline_and_warns(self):
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
        )
        assert service.replication_queue_service is None

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            notification = self._create(service, send_after=_future())

        # No queue configured, so every backend replicates inline rather than silently dropping.
        assert any(
            "no replication queue service is configured" in message for message in captured.output
        )
        for replica in (self.replica_one, self.replica_two):
            assert replica.get_notification(notification.id).id == notification.id

    def test_replication_mode_resolves_from_settings_default_to_inline(self):
        # Bare-Python host: the setting reads as {}, so an unset mode resolves to inline.
        service = self.build_service(additional_backends=[self.replica_one])
        assert service.replication_mode == "inline"

    def test_replication_mode_resolves_queued_from_settings(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queued"}):
                service = self.build_service(additional_backends=[self.replica_one])

        assert service.replication_mode == "queued"

    def test_explicit_replication_mode_wins_over_queued_setting(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queued"}):
                service = self.build_service(
                    additional_backends=[self.replica_one], replication_mode="inline"
                )

        assert service.replication_mode == "inline"

    def test_invalid_replication_mode_setting_raises(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queded"}):
                with self.assertRaises(NotificationError):
                    self.build_service(additional_backends=[self.replica_one])

    def test_register_replication_queue_service_injects_after_construction(self):
        service = self.build_service(
            additional_backends=[self.replica_one], replication_mode="queued"
        )
        assert service.replication_queue_service is None

        service.register_replication_queue_service(self.queue)

        notification = self._create(service, send_after=_future())
        assert self.queue.enqueued_replications == [(notification.id, "backend-1")]

    def test_replication_queue_service_is_a_base_replication_queue_service(self):
        service = self.build_service(
            additional_backends=[self.replica_one],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )
        assert isinstance(
            service.replication_queue_service, BaseNotificationReplicationQueueService
        )


class AsyncIOQueuedReplicationTestCase(IsolatedAsyncioTestCase):
    """AsyncIO ``AsyncIONotificationService`` queued replication + fallback (Phase 3)."""

    def setUp(self):
        register_context("multi_backend_replication_test_context")(_build_context)
        self.primary_backend = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_one = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_two = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.queue = FakeAsyncIOReplicationQueueService()
        self._owned_backends = [self.primary_backend, self.replica_one, self.replica_two]

    async def asyncTearDown(self):
        for backend in self._owned_backends:
            await backend.clear()

    def build_service(self, **kwargs) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [ASYNCIO_IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return AsyncIONotificationService(**kwargs)

    async def _create(
        self, service: AsyncIONotificationService, send_after=None, title="Notification"
    ):
        return await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_replication_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    async def test_queued_mode_enqueues_one_task_per_additional_backend(self):
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )

        notification = await self._create(service, send_after=_future())

        assert self.queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-2"),
        ]
        with self.assertRaises(NotificationNotFoundError):
            await self.replica_one.get_notification(notification.id)
        with self.assertRaises(NotificationNotFoundError):
            await self.replica_two.get_notification(notification.id)

    async def test_missing_id_falls_back_to_inline_and_enqueues_nothing(self):
        inline_service = self.build_service(additional_backends=[self.replica_one])
        notifications = [await self._create(inline_service, title=f"n{i}") for i in range(2)]
        ids = [n.id for n in notifications]

        queued_service = self.build_service(
            additional_backends=[self.replica_one],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            await queued_service.mark_read_bulk(ids, user_id=1)

        assert self.queue.enqueued_replications == []
        assert any("falling back to inline replication" in message for message in captured.output)
        for notification_id in ids:
            assert (await self.replica_one.get_notification(notification_id)).status == (
                NotificationStatus.READ.value
            )

    async def test_enqueue_error_inline_replicates_only_the_failed_backend(self):
        queue = _AsyncIORaisingForBackendQueueService(failing_backend_identifier="backend-2")
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=queue,
        )

        notification = await self._create(service, send_after=_future())

        assert queue.enqueued_replications == [(notification.id, "backend-1")]
        with self.assertRaises(NotificationNotFoundError):
            await self.replica_one.get_notification(notification.id)
        assert (await self.replica_two.get_notification(notification.id)).id == notification.id

    async def test_enqueue_error_on_middle_backend_still_enqueues_the_one_after_it(self):
        replica_three = FakeAsyncIOFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(replica_three)
        queue = _AsyncIORaisingForBackendQueueService(failing_backend_identifier="backend-2")
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two, replica_three],
            replication_mode="queued",
            replication_queue_service=queue,
        )

        notification = await self._create(service, send_after=_future())

        # backend-1 and backend-3 enqueued cleanly; backend-2's enqueue raised (and was
        # replicated inline instead), proving a failing middle backend does not stop the loop
        # from reaching the backend after it.
        assert queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-3"),
        ]
        with self.assertRaises(NotificationNotFoundError):
            await self.replica_one.get_notification(notification.id)
        assert (await self.replica_two.get_notification(notification.id)).id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            await replica_three.get_notification(notification.id)

    async def test_no_queue_service_falls_back_to_inline_and_warns(self):
        service = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
        )
        assert service.replication_queue_service is None

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            notification = await self._create(service, send_after=_future())

        assert any(
            "no replication queue service is configured" in message for message in captured.output
        )
        for replica in (self.replica_one, self.replica_two):
            assert (await replica.get_notification(notification.id)).id == notification.id

    async def test_replication_mode_resolves_from_settings_default_to_inline(self):
        service = self.build_service(additional_backends=[self.replica_one])
        assert service.replication_mode == "inline"

    async def test_replication_mode_resolves_queued_from_settings(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queued"}):
                service = self.build_service(additional_backends=[self.replica_one])

        assert service.replication_mode == "queued"

    async def test_explicit_replication_mode_wins_over_queued_setting(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queued"}):
                service = self.build_service(
                    additional_backends=[self.replica_one], replication_mode="inline"
                )

        assert service.replication_mode == "inline"

    async def test_invalid_replication_mode_setting_raises(self):
        _reset_notification_settings_singleton(self)
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_REPLICATION_MODE": "queded"}):
                with self.assertRaises(NotificationError):
                    self.build_service(additional_backends=[self.replica_one])

    async def test_register_replication_queue_service_injects_after_construction(self):
        service = self.build_service(
            additional_backends=[self.replica_one], replication_mode="queued"
        )
        assert service.replication_queue_service is None

        await service.register_replication_queue_service(self.queue)

        notification = await self._create(service, send_after=_future())
        assert self.queue.enqueued_replications == [(notification.id, "backend-1")]

    async def test_replication_queue_service_is_a_base_replication_queue_service(self):
        service = self.build_service(
            additional_backends=[self.replica_one],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )
        assert isinstance(
            service.replication_queue_service, AsyncIOBaseNotificationReplicationQueueService
        )


class ProcessReplicationTestCase(TestCase):
    """Sync ``process_replication`` worker entrypoint (Phase 3)."""

    def setUp(self):
        register_context("multi_backend_replication_test_context")(_build_context)
        self.primary_backend = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_one = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_two = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.queue = FakeReplicationQueueService()
        self._owned_backends = [self.primary_backend, self.replica_one, self.replica_two]

    def tearDown(self):
        for backend in self._owned_backends:
            backend.clear()

    def build_service(self, **kwargs) -> NotificationService:
        kwargs.setdefault("notification_adapters", [IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return NotificationService(**kwargs)

    def _create(self, service: NotificationService, send_after=None, title="Notification"):
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_replication_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    def test_process_replication_applies_snapshot_to_all_targets(self):
        # Create on the primary only, then replicate to two empty replicas.
        primary_only = self.build_service()
        notification = self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = worker.process_replication(notification.id)

        assert result == {"successes": ["backend-1", "backend-2"], "failures": []}
        for replica in (self.replica_one, self.replica_two):
            assert replica.get_notification(notification.id).title == notification.title

    def test_process_replication_applies_snapshot_to_a_single_named_target(self):
        primary_only = self.build_service()
        notification = self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = worker.process_replication(notification.id, "backend-2")

        assert result == {"successes": ["backend-2"], "failures": []}
        assert self.replica_two.get_notification(notification.id).id == notification.id
        # The other replica was not targeted.
        with self.assertRaises(NotificationNotFoundError):
            self.replica_one.get_notification(notification.id)

    def test_process_replication_reports_failure_for_declining_backend_without_the_row(self):
        # A backend that declines snapshot application (apply_replication_snapshot_if_newer ->
        # applied=False) and lacks the row cannot be created inline with the primary's id: the
        # convergence path logs-and-skips instead of raising, so process_replication must still
        # classify it as a failure rather than reporting a false success.
        primary_only = self.build_service()
        notification = self._create(primary_only, send_after=_future())

        declining = NoSnapshotApplyReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(declining)

        worker = self.build_service(additional_backends=[self.replica_one, declining])
        result = worker.process_replication(notification.id)

        assert result["successes"] == ["backend-1"]
        assert result["failures"] == [
            {
                "backend_identifier": "backend-2",
                "error": (
                    "replica lacks apply_replication_snapshot_if_newer and could not be "
                    "populated with the primary id"
                ),
            }
        ]
        assert self.replica_one.get_notification(notification.id).id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            declining.get_notification(notification.id)

    def test_process_replication_unknown_target_raises(self):
        primary_only = self.build_service()
        notification = self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            worker.process_replication(notification.id, "backend-does-not-exist")

    def test_process_replication_missing_on_primary_raises_replication_error(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(ReplicationError):
            worker.process_replication("no-such-id")

    def test_replicate_notification_is_the_no_target_alias(self):
        primary_only = self.build_service()
        notification = self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = worker.replicate_notification(notification.id)

        assert result == {"successes": ["backend-1", "backend-2"], "failures": []}
        for replica in (self.replica_one, self.replica_two):
            assert replica.get_notification(notification.id).id == notification.id

    def test_web_enqueue_then_worker_drain_converges_replica(self):
        # Web path: create under queued mode, enqueuing one task per replica.
        web = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )
        notification = self._create(web, send_after=_future())
        assert self.queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-2"),
        ]

        # Worker path: drain each enqueued task through process_replication.
        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        for notification_id, backend_identifier in self.queue.enqueued_replications:
            worker.process_replication(notification_id, backend_identifier)

        for backend in (self.primary_backend, self.replica_one, self.replica_two):
            assert backend.get_notification(notification.id).title == notification.title


class AsyncIOProcessReplicationTestCase(IsolatedAsyncioTestCase):
    """AsyncIO ``process_replication`` worker entrypoint (Phase 3)."""

    def setUp(self):
        register_context("multi_backend_replication_test_context")(_build_context)
        self.primary_backend = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_one = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_two = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.queue = FakeAsyncIOReplicationQueueService()
        self._owned_backends = [self.primary_backend, self.replica_one, self.replica_two]

    async def asyncTearDown(self):
        for backend in self._owned_backends:
            await backend.clear()

    def build_service(self, **kwargs) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [ASYNCIO_IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return AsyncIONotificationService(**kwargs)

    async def _create(
        self, service: AsyncIONotificationService, send_after=None, title="Notification"
    ):
        return await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_replication_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    async def test_process_replication_applies_snapshot_to_all_targets(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = await worker.process_replication(notification.id)

        assert result == {"successes": ["backend-1", "backend-2"], "failures": []}
        for replica in (self.replica_one, self.replica_two):
            assert (await replica.get_notification(notification.id)).title == notification.title

    async def test_process_replication_applies_snapshot_to_a_single_named_target(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = await worker.process_replication(notification.id, "backend-2")

        assert result == {"successes": ["backend-2"], "failures": []}
        assert (await self.replica_two.get_notification(notification.id)).id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            await self.replica_one.get_notification(notification.id)

    async def test_process_replication_reports_failure_for_declining_backend_without_the_row(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only, send_after=_future())

        declining = AsyncIONoSnapshotApplyReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(declining)

        worker = self.build_service(additional_backends=[self.replica_one, declining])
        result = await worker.process_replication(notification.id)

        assert result["successes"] == ["backend-1"]
        assert result["failures"] == [
            {
                "backend_identifier": "backend-2",
                "error": (
                    "replica lacks apply_replication_snapshot_if_newer and could not be "
                    "populated with the primary id"
                ),
            }
        ]
        assert (await self.replica_one.get_notification(notification.id)).id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            await declining.get_notification(notification.id)

    async def test_process_replication_unknown_target_raises(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            await worker.process_replication(notification.id, "backend-does-not-exist")

    async def test_process_replication_missing_on_primary_raises_replication_error(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(ReplicationError):
            await worker.process_replication("no-such-id")

    async def test_replicate_notification_is_the_no_target_alias(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only, send_after=_future())

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        result = await worker.replicate_notification(notification.id)

        assert result == {"successes": ["backend-1", "backend-2"], "failures": []}
        for replica in (self.replica_one, self.replica_two):
            assert (await replica.get_notification(notification.id)).id == notification.id

    async def test_web_enqueue_then_worker_drain_converges_replica(self):
        web = self.build_service(
            additional_backends=[self.replica_one, self.replica_two],
            replication_mode="queued",
            replication_queue_service=self.queue,
        )
        notification = await self._create(web, send_after=_future())
        assert self.queue.enqueued_replications == [
            (notification.id, "backend-1"),
            (notification.id, "backend-2"),
        ]

        worker = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        for notification_id, backend_identifier in self.queue.enqueued_replications:
            await worker.process_replication(notification_id, backend_identifier)

        for backend in (self.primary_backend, self.replica_one, self.replica_two):
            assert (await backend.get_notification(notification.id)).title == notification.title
