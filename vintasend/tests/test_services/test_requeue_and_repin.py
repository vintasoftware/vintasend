"""Requeueing a notification that did not go out, and repinning one to a template version.

``requeue_notification`` moves a ``FAILED`` or ``CANCELLED`` notification back to
``PENDING_SEND`` and sends it when it is due. ``repin_notification`` changes
``requested_template_version`` on a notification that was not delivered. Both refuse with
``NotificationActionError`` before anything is written.

Every case runs against the sync service and again against the AsyncIO one, and the fake
backends' own ``requeue_notification`` is covered at the end of the module.
"""

import datetime
import logging
import tempfile
import uuid
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from freezegun import freeze_time

from vintasend.constants import (
    REPINNABLE_NOTIFICATION_STATUSES,
    REQUEUEABLE_NOTIFICATION_STATUSES,
    NotificationStatus,
    NotificationTypes,
)
from vintasend.exceptions import (
    InvalidTemplateVersionError,
    NotificationActionError,
    NotificationCancelError,
    NotificationError,
    NotificationNotFoundError,
    NotificationRequeueError,
)
from vintasend.services.attachment_managers.stubs.fake_attachment_manager import (
    FakeAsyncIOAttachmentManager,
    FakeAttachmentManager,
)
from vintasend.services.dataclasses import (
    Notification,
    NotificationAttachment,
    NotificationContextDict,
    OneOffNotification,
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
    FakeVersionedTemplateRenderer,
)


VERSIONED_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeVersionedTemplateRenderer"
)
RAISING_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeRaisingVersionTemplateRenderer"
)
PLAIN_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeTemplateRenderer"
)
SYNC_ADAPTER = "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter"
SYNC_BACKGROUND_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter"
)
ASYNCIO_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter"
)
ASYNCIO_BACKGROUND_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOBackgroundEmailAdapter"
)

SERVICE_LOGGER = "vintasend.services.notification_service"

# Field values that must never reach a log line or an error message.
SENSITIVE_TITLE = "Lab results for Jane Roe"
SENSITIVE_BODY_TEMPLATE = "diagnosis-letter"

PENDING = NotificationStatus.PENDING_SEND.value
SENT = NotificationStatus.SENT.value
FAILED = NotificationStatus.FAILED.value
READ = NotificationStatus.READ.value
CANCELLED = NotificationStatus.CANCELLED.value
ALL_STATUSES = (PENDING, SENT, FAILED, READ, CANCELLED)


@register_context("requeue_repin_test_context")
def _requeue_repin_test_context() -> NotificationContextDict:
    return NotificationContextDict({"source": "generated"})


@register_context("requeue_repin_raising_context")
def _requeue_repin_raising_context() -> NotificationContextDict:
    raise RuntimeError("the context must not be generated again")


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc)


def _future() -> datetime.datetime:
    return _now() + datetime.timedelta(days=1)


def _past() -> datetime.datetime:
    return _now() - datetime.timedelta(days=1)


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    # Created in the future, so creating it sends nothing and a test decides the status.
    defaults: dict[str, Any] = {
        "user_id": 1,
        "notification_type": NotificationTypes.EMAIL.value,
        "title": SENSITIVE_TITLE,
        "body_template": SENSITIVE_BODY_TEMPLATE,
        "context_name": "requeue_repin_test_context",
        "context_kwargs": NotificationContextDict({}),
        "send_after": _future(),
        "subject_template": "subject",
        "preheader_template": "preheader",
    }
    defaults.update(overrides)
    return defaults


def _one_off_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults = _create_kwargs(**overrides)
    defaults.pop("user_id")
    defaults.update(
        {"email_or_phone": "someone@example.com", "first_name": "Some", "last_name": "One"}
    )
    return defaults


def _move_to(backend: FakeFileBackend, notification_id: Any, status: str) -> None:
    """Put a pending notification in ``status`` with the backend's own transitions."""
    if status == FAILED:
        backend.mark_pending_as_failed(notification_id)
    elif status == CANCELLED:
        backend.cancel_notification(notification_id)
    elif status in (SENT, READ):
        backend.mark_pending_as_sent(notification_id)
        if status == READ:
            backend.mark_sent_as_read(notification_id)


async def _amove_to(backend: FakeAsyncIOFileBackend, notification_id: Any, status: str) -> None:
    if status == FAILED:
        await backend.mark_pending_as_failed(notification_id)
    elif status == CANCELLED:
        await backend.cancel_notification(notification_id)
    elif status in (SENT, READ):
        await backend.mark_pending_as_sent(notification_id)
        if status == READ:
            await backend.mark_sent_as_read(notification_id)


class _FailingReplica(FakeFileBackend):
    """A replica that refuses every write the service replicates to it."""

    def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("replica down")

    def requeue_notification(self, notification_id, send_after=None):
        raise RuntimeError("replica down")

    def persist_notification_update(self, notification_id, update_data):
        raise RuntimeError("replica down")


class _AsyncIOFailingReplica(FakeAsyncIOFileBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("replica down")

    async def requeue_notification(self, notification_id, send_after=None, lock=None):
        raise RuntimeError("replica down")

    async def persist_notification_update(self, notification_id, update_data, lock=None):
        raise RuntimeError("replica down")


class _NoSnapshotReplica(FakeFileBackend):
    """A replica that declines snapshots, so the service replicates with its primitives."""

    def apply_replication_snapshot_if_newer(self, snapshot):
        from vintasend.services.dataclasses import ApplyResult

        return ApplyResult(applied=False, reason="declined for test")


class _AsyncIONoSnapshotReplica(FakeAsyncIOFileBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        from vintasend.services.dataclasses import ApplyResult

        return ApplyResult(applied=False, reason="declined for test")


def _new_backend(cls: type) -> Any:
    return cls(database_file_name=tempfile.mktemp(suffix=".json"))


class _RecordingHandler(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__()
        self.records = records

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _LogCapture:
    """Every record the service logs, so a test can check no field value leaked into one."""

    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []
        self._handler = _RecordingHandler(self.records)
        self._logger = logging.getLogger(SERVICE_LOGGER)
        self._previous_level = self._logger.level

    def __enter__(self) -> "_LogCapture":
        self._logger.addHandler(self._handler)
        self._logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *_: object) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous_level)

    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)


def _sent(service: Any) -> list:
    """What the service's one fake email adapter delivered: (notification, context, ...)."""
    return next(iter(service.notification_adapters)).sent_emails


def _assert_no_field_values(text: str) -> None:
    assert SENSITIVE_TITLE not in text
    assert SENSITIVE_BODY_TEMPLATE not in text
    assert "someone@example.com" not in text


# --- sync service ------------------------------------------------------------------------


class RequeueNotificationTestCase(TestCase):
    def setUp(self):
        self.backend = _new_backend(FakeFileBackend)
        self._backends = [self.backend]

    def tearDown(self):
        for backend in self._backends:
            backend.clear()

    def build_service(self, adapter: str = SYNC_ADAPTER, **kwargs: Any) -> NotificationService:
        kwargs.setdefault("notification_adapters", [(adapter, PLAIN_RENDERER)])
        kwargs.setdefault("notification_backend", self.backend)
        return NotificationService(**kwargs)

    def make(self, service: NotificationService, status: str, **overrides: Any) -> Any:
        notification = service.create_notification(**_create_kwargs(**overrides))
        _move_to(self.backend, notification.id, status)
        return self.backend.get_notification(notification.id)

    # -- statuses ---------------------------------------------------------

    def test_requeueable_statuses_are_failed_and_cancelled(self):
        assert REQUEUEABLE_NOTIFICATION_STATUSES == (FAILED, CANCELLED)

    def test_requeues_each_allowed_status_and_sends_it_when_due(self):
        for status in REQUEUEABLE_NOTIFICATION_STATUSES:
            with self.subTest(status=status):
                service = self.build_service()
                notification = self.make(service, status)

                requeued = service.requeue_notification(notification.id, send_after=None)

                assert requeued.id == notification.id
                assert requeued.status == SENT
                assert self.backend.get_notification(notification.id).status == SENT
                assert [n.id for n, _, _ in _sent(service)] == [notification.id]

    def test_refuses_every_other_status_before_writing(self):
        for status in (PENDING, SENT, READ):
            with self.subTest(status=status):
                service = self.build_service()
                notification = self.make(service, status)
                modified = notification.modified

                with patch.object(self.backend, "requeue_notification") as backend_requeue:
                    with self.assertRaises(NotificationActionError) as raised:
                        service.requeue_notification(notification.id, send_after=None)

                backend_requeue.assert_not_called()
                assert raised.exception.action == "requeue"
                assert raised.exception.reason == "INVALID_STATUS"
                assert raised.exception.status == status
                assert raised.exception.notification_id == notification.id
                assert str(notification.id) in str(raised.exception)
                assert self.backend.get_notification(notification.id).modified == modified
                assert _sent(service) == []

    def test_unknown_id_is_not_found(self):
        service = self.build_service()

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification("missing")

        assert raised.exception.reason == "NOT_FOUND"
        assert raised.exception.action == "requeue"
        assert raised.exception.status is None
        assert str(raised.exception) == "Notification missing not found"

    def test_a_status_changed_by_another_caller_is_refused(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        with patch.object(
            self.backend,
            "requeue_notification",
            side_effect=NotificationRequeueError("raced"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                service.requeue_notification(notification.id)

        assert raised.exception.reason == "INVALID_STATUS"
        assert raised.exception.status is None

    # -- send_after -----------------------------------------------------------

    def test_send_after_left_out_keeps_a_future_schedule(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(notification.id)

        assert requeued.send_after == notification.send_after
        assert self.backend.get_notification(notification.id).status == PENDING
        assert _sent(service) == []

    def test_send_after_left_out_with_a_past_schedule_sends_now(self):
        with freeze_time("2030-01-01T00:00:00Z"):
            service = self.build_service()
            notification = self.make(
                service,
                FAILED,
                send_after=datetime.datetime(2030, 1, 1, 1, tzinfo=datetime.timezone.utc),
            )

        with freeze_time("2030-01-01T02:00:00Z"):
            requeued = service.requeue_notification(notification.id)

        assert requeued.send_after == notification.send_after
        assert self.backend.get_notification(notification.id).status == SENT

    def test_send_after_none_sends_now(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(notification.id, send_after=None)

        assert requeued.send_after is None
        assert self.backend.get_notification(notification.id).status == SENT

    def test_send_after_in_the_past_sends_now(self):
        service = self.build_service()
        notification = self.make(service, CANCELLED)
        past = _past()

        requeued = service.requeue_notification(notification.id, send_after=past)

        assert requeued.send_after == past
        assert self.backend.get_notification(notification.id).status == SENT

    def test_send_after_in_the_future_reschedules_without_sending(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        later = _future() + datetime.timedelta(days=3)

        requeued = service.requeue_notification(notification.id, send_after=later)

        assert requeued.send_after == later
        stored = self.backend.get_notification(notification.id)
        assert stored.status == PENDING
        assert stored.send_after == later
        assert _sent(service) == []

    # -- what it changes and keeps --------------------------------------------

    def test_returns_the_notification_after_the_send(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(notification.id, send_after=None)

        assert requeued.status == SENT
        assert requeued.sent_at is not None
        assert self.backend.get_notification(notification.id).status == SENT

    def test_clears_sent_at_and_keeps_everything_else(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        stored = self.backend.get_notification(notification.id)
        stored.sent_at = _past()
        stored.requested_template_version = 4
        stored.context_used = {"source": "stored"}
        stored.adapter_used = "some.adapter"
        stored.git_commit_sha = "a" * 40

        requeued = service.requeue_notification(notification.id)

        assert requeued.sent_at is None
        assert requeued.requested_template_version == 4
        assert requeued.context_used == {"source": "stored"}
        assert requeued.adapter_used == "some.adapter"
        assert requeued.git_commit_sha == "a" * 40
        assert requeued.title == SENSITIVE_TITLE

    def test_sends_with_the_stored_context_instead_of_generating_one(self):
        service = self.build_service()
        notification = self.make(service, FAILED, context_name="requeue_repin_raising_context")
        self.backend.get_notification(notification.id).context_used = {"source": "stored"}

        service.requeue_notification(notification.id, send_after=None)

        assert self.backend.get_notification(notification.id).status == SENT
        assert [context for _, context, _ in _sent(service)] == [{"source": "stored"}]

    def test_generates_the_context_when_none_is_stored(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        service.requeue_notification(notification.id, send_after=None)

        assert [context for _, context, _ in _sent(service)] == [{"source": "generated"}]

    def test_a_queue_adapter_puts_it_back_on_the_queue(self):
        queue_service = FakeQueueService()
        service = self.build_service(
            adapter=SYNC_BACKGROUND_ADAPTER, notification_queue_service=queue_service
        )
        notification = self.make(service, FAILED)

        service.requeue_notification(notification.id, send_after=None)

        assert queue_service.enqueued_notification_ids == [notification.id]
        assert self.backend.get_notification(notification.id).status == PENDING

    def test_requeues_a_one_off_notification(self):
        service = self.build_service()
        created = service.create_one_off_notification(**_one_off_kwargs())
        _move_to(self.backend, created.id, FAILED)

        requeued = service.requeue_notification(created.id, send_after=None)

        assert isinstance(requeued, OneOffNotification)
        assert requeued.status == SENT
        assert self.backend.get_notification(created.id).status == SENT

    def test_a_cancelled_then_requeued_notification_can_be_sent(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs())
        service.cancel_notification(created.id)

        service.requeue_notification(created.id, send_after=None)

        assert self.backend.get_notification(created.id).status == SENT

    # -- replication ----------------------------------------------------------

    def test_writes_to_the_primary_then_to_every_additional_backend(self):
        replica_one = _new_backend(FakeFileBackend)
        replica_two = _new_backend(_NoSnapshotReplica)
        self._backends += [replica_one, replica_two]
        service = self.build_service(additional_backends=[replica_one, replica_two])
        created = service.create_notification(**_create_kwargs())
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        FakeFileBackend.apply_replication_snapshot_if_newer(replica_two, created)
        for backend in self._backends:
            _move_to(backend, created.id, FAILED)
        later = _future() + datetime.timedelta(days=2)

        service.requeue_notification(created.id, send_after=later)

        for backend in self._backends:
            stored = backend.get_notification(created.id)
            assert stored.status == PENDING
            assert stored.send_after == later

    def test_a_failing_replica_does_not_change_the_primary_result(self):
        replica = _new_backend(_FailingReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())
        _move_to(self.backend, created.id, FAILED)

        with self.assertLogs(SERVICE_LOGGER, level="ERROR"):
            requeued = service.requeue_notification(created.id, send_after=None)

        assert requeued.status == SENT
        assert self.backend.get_notification(created.id).status == SENT

    # -- privacy --------------------------------------------------------------

    def test_logs_and_errors_name_the_notification_by_id_only(self):
        service = self.build_service()
        failed = self.make(service, FAILED)
        sent = self.make(service, SENT)

        with _LogCapture() as logs:
            service.requeue_notification(failed.id, send_after=None)
            with self.assertRaises(NotificationActionError) as raised:
                service.requeue_notification(sent.id)

        assert str(failed.id) in logs.text()
        _assert_no_field_values(logs.text())
        _assert_no_field_values(str(raised.exception))


class RepinNotificationTestCase(TestCase):
    def setUp(self):
        self.backend = _new_backend(FakeFileBackend)
        self._backends = [self.backend]
        FakeVersionedTemplateRenderer.latest_version = 7

    def tearDown(self):
        for backend in self._backends:
            backend.clear()
        FakeVersionedTemplateRenderer.latest_version = 7

    def build_service(
        self, renderer: str = VERSIONED_RENDERER, **kwargs: Any
    ) -> NotificationService:
        kwargs.setdefault("notification_adapters", [(SYNC_ADAPTER, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return NotificationService(**kwargs)

    def make(self, service: NotificationService, status: str, **overrides: Any) -> Any:
        notification = service.create_notification(**_create_kwargs(**overrides))
        _move_to(self.backend, notification.id, status)
        return self.backend.get_notification(notification.id)

    def test_repinnable_statuses_are_the_undelivered_ones(self):
        assert REPINNABLE_NOTIFICATION_STATUSES == (PENDING, FAILED, CANCELLED)

    def test_pins_the_given_version_in_each_allowed_status(self):
        for status in REPINNABLE_NOTIFICATION_STATUSES:
            with self.subTest(status=status):
                service = self.build_service()
                notification = self.make(service, status)

                repinned = service.repin_notification(notification.id, template_version=3)

                assert repinned.requested_template_version == 3
                stored = self.backend.get_notification(notification.id)
                assert stored.requested_template_version == 3
                # It neither sends nor changes the status.
                assert stored.status == status
                assert _sent(service) == []  # type: ignore[union-attr]

    def test_refuses_a_delivered_notification_before_writing(self):
        for status in (SENT, READ):
            with self.subTest(status=status):
                service = self.build_service()
                notification = self.make(service, status)

                with patch.object(self.backend, "persist_notification_update") as update:
                    with self.assertRaises(NotificationActionError) as raised:
                        service.repin_notification(notification.id, template_version=3)

                update.assert_not_called()
                assert raised.exception.action == "repin"
                assert raised.exception.reason == "INVALID_STATUS"
                assert raised.exception.status == status

    def test_pins_the_current_version_when_none_is_given(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        FakeVersionedTemplateRenderer.latest_version = 9

        repinned = service.repin_notification(notification.id)

        assert repinned.requested_template_version == 9

    def test_moves_an_existing_pin_onto_the_current_version(self):
        service = self.build_service()
        notification = self.make(service, PENDING, requested_template_version=2)
        assert notification.requested_template_version == 2

        repinned = service.repin_notification(notification.id)

        assert repinned.requested_template_version == 7

    def test_version_zero_is_a_version(self):
        service = self.build_service()
        notification = self.make(service, PENDING, requested_template_version=5)

        repinned = service.repin_notification(notification.id, template_version=0)

        assert repinned.requested_template_version == 0

    def test_repinning_to_the_pinned_version_writes_nothing(self):
        service = self.build_service()
        notification = self.make(service, FAILED, requested_template_version=7)

        with patch.object(self.backend, "persist_notification_update") as update:
            repinned = service.repin_notification(notification.id)

        update.assert_not_called()
        assert repinned.requested_template_version == 7

    def test_refuses_when_the_renderer_reports_no_version(self):
        service = self.build_service(renderer=PLAIN_RENDERER)
        notification = self.make(service, FAILED)

        with self.assertRaises(NotificationActionError) as raised:
            service.repin_notification(notification.id)

        assert raised.exception.reason == "NO_TEMPLATE_VERSION"
        assert raised.exception.action == "repin"
        assert self.backend.get_notification(notification.id).requested_template_version is None

    def test_a_renderer_that_raises_is_logged_and_counts_as_no_version(self):
        service = self.build_service(renderer=RAISING_RENDERER)
        notification = self.make(service, FAILED)

        with _LogCapture() as logs:
            with self.assertRaises(NotificationActionError) as raised:
                service.repin_notification(notification.id)

        assert raised.exception.reason == "NO_TEMPLATE_VERSION"
        assert str(notification.id) in str(raised.exception)
        assert "RuntimeError" in logs.text()
        # The renderer's own message stays out of the log: it can quote template content.
        assert "unreachable" not in logs.text()
        _assert_no_field_values(logs.text())
        _assert_no_field_values(str(raised.exception))

    def test_an_invalid_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        for template_version in (-1, 1.5, "3", True):
            with self.subTest(template_version=template_version):
                with patch.object(self.backend, "get_notification") as get_notification:
                    with self.assertRaises(InvalidTemplateVersionError) as raised:
                        service.repin_notification(
                            "any",
                            template_version=template_version,  # type: ignore[arg-type]
                        )

                get_notification.assert_not_called()
                assert isinstance(raised.exception, ValueError)
                assert isinstance(raised.exception, NotificationError)

    def test_unknown_id_is_not_found(self):
        service = self.build_service()

        with self.assertRaises(NotificationActionError) as raised:
            service.repin_notification("missing", template_version=1)

        assert raised.exception.reason == "NOT_FOUND"
        assert raised.exception.action == "repin"

    def test_repins_a_one_off_notification(self):
        service = self.build_service()
        created = service.create_one_off_notification(**_one_off_kwargs())

        repinned = service.repin_notification(created.id, template_version=2)

        assert isinstance(repinned, OneOffNotification)
        assert repinned.requested_template_version == 2

    def test_writes_to_every_backend_including_one_that_declines_snapshots(self):
        replica_one = _new_backend(FakeFileBackend)
        replica_two = _new_backend(_NoSnapshotReplica)
        self._backends += [replica_one, replica_two]
        service = self.build_service(additional_backends=[replica_one, replica_two])
        created = service.create_notification(**_create_kwargs())
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        FakeFileBackend.apply_replication_snapshot_if_newer(replica_two, created)

        service.repin_notification(created.id, template_version=4)

        for backend in self._backends:
            assert backend.get_notification(created.id).requested_template_version == 4

    def test_a_failing_replica_does_not_change_the_primary_result(self):
        replica = _new_backend(_FailingReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())

        with self.assertLogs(SERVICE_LOGGER, level="ERROR"):
            repinned = service.repin_notification(created.id, template_version=4)

        assert repinned.requested_template_version == 4
        assert self.backend.get_notification(created.id).requested_template_version == 4

    def test_update_notification_does_not_send_a_failed_notification(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        service.update_notification(notification.id, send_after=None)

        assert self.backend.get_notification(notification.id).status == FAILED
        assert _sent(service) == []  # type: ignore[union-attr]


# --- AsyncIO service ---------------------------------------------------------------------


class AsyncIORequeueNotificationTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = _new_backend(FakeAsyncIOFileBackend)
        self._backends = [self.backend]

    async def asyncTearDown(self):
        for backend in self._backends:
            await backend.clear()

    def build_service(
        self, adapter: str = ASYNCIO_ADAPTER, **kwargs: Any
    ) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [(adapter, PLAIN_RENDERER)])
        kwargs.setdefault("notification_backend", self.backend)
        return AsyncIONotificationService(**kwargs)

    async def make(self, service: AsyncIONotificationService, status: str, **overrides: Any):
        notification = await service.create_notification(**_create_kwargs(**overrides))
        await _amove_to(self.backend, notification.id, status)
        return await self.backend.get_notification(notification.id)

    async def test_requeues_each_allowed_status_and_sends_it_when_due(self):
        for status in REQUEUEABLE_NOTIFICATION_STATUSES:
            with self.subTest(status=status):
                service = self.build_service()
                notification = await self.make(service, status)

                requeued = await service.requeue_notification(notification.id, send_after=None)

                assert requeued.id == notification.id
                assert requeued.status == SENT
                assert (await self.backend.get_notification(notification.id)).status == SENT
                assert [n.id for n, _, _ in _sent(service)] == [notification.id]

    async def test_refuses_every_other_status_before_writing(self):
        for status in (PENDING, SENT, READ):
            with self.subTest(status=status):
                service = self.build_service()
                notification = await self.make(service, status)

                with patch.object(self.backend, "requeue_notification") as backend_requeue:
                    with self.assertRaises(NotificationActionError) as raised:
                        await service.requeue_notification(notification.id, send_after=None)

                backend_requeue.assert_not_called()
                assert raised.exception.reason == "INVALID_STATUS"
                assert raised.exception.status == status
                assert _sent(service) == []

    async def test_unknown_id_is_not_found(self):
        service = self.build_service()

        with self.assertRaises(NotificationActionError) as raised:
            await service.requeue_notification("missing")

        assert raised.exception.reason == "NOT_FOUND"
        assert str(raised.exception) == "Notification missing not found"

    async def test_a_status_changed_by_another_caller_is_refused(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        with patch.object(
            self.backend,
            "requeue_notification",
            side_effect=NotificationRequeueError("raced"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                await service.requeue_notification(notification.id)

        assert raised.exception.reason == "INVALID_STATUS"

    async def test_send_after_left_out_keeps_a_future_schedule(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        requeued = await service.requeue_notification(notification.id)

        assert requeued.send_after == notification.send_after
        assert (await self.backend.get_notification(notification.id)).status == PENDING
        assert _sent(service) == []

    async def test_send_after_left_out_with_a_past_schedule_sends_now(self):
        with freeze_time("2030-01-01T00:00:00Z"):
            service = self.build_service()
            notification = await self.make(
                service,
                FAILED,
                send_after=datetime.datetime(2030, 1, 1, 1, tzinfo=datetime.timezone.utc),
            )

        with freeze_time("2030-01-01T02:00:00Z"):
            await service.requeue_notification(notification.id)

        assert (await self.backend.get_notification(notification.id)).status == SENT

    async def test_send_after_none_or_past_sends_now(self):
        for send_after in (None, _past()):
            with self.subTest(send_after=send_after):
                service = self.build_service()
                notification = await self.make(service, FAILED)

                requeued = await service.requeue_notification(
                    notification.id, send_after=send_after
                )

                assert requeued.send_after == send_after
                assert (await self.backend.get_notification(notification.id)).status == SENT

    async def test_send_after_in_the_future_reschedules_without_sending(self):
        service = self.build_service()
        notification = await self.make(service, CANCELLED)
        later = _future() + datetime.timedelta(days=3)

        requeued = await service.requeue_notification(notification.id, send_after=later)

        assert requeued.send_after == later
        assert (await self.backend.get_notification(notification.id)).status == PENDING
        assert _sent(service) == []

    async def test_clears_sent_at_and_keeps_everything_else(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        stored = await self.backend.get_notification(notification.id)
        stored.sent_at = _past()
        stored.requested_template_version = 4
        stored.context_used = {"source": "stored"}
        stored.adapter_used = "some.adapter"
        stored.git_commit_sha = "a" * 40

        requeued = await service.requeue_notification(notification.id)

        assert requeued.sent_at is None
        assert requeued.requested_template_version == 4
        assert requeued.context_used == {"source": "stored"}
        assert requeued.adapter_used == "some.adapter"
        assert requeued.git_commit_sha == "a" * 40

    async def test_sends_with_the_stored_context_instead_of_generating_one(self):
        service = self.build_service()
        notification = await self.make(
            service, FAILED, context_name="requeue_repin_raising_context"
        )
        (await self.backend.get_notification(notification.id)).context_used = {"source": "stored"}

        requeued = await service.requeue_notification(notification.id, send_after=None)

        assert requeued.status == SENT
        assert [context for _, context, _ in _sent(service)] == [{"source": "stored"}]

    async def test_a_queue_adapter_puts_it_back_on_the_queue(self):
        queue_service = FakeAsyncIOQueueService()
        service = self.build_service(
            adapter=ASYNCIO_BACKGROUND_ADAPTER, notification_queue_service=queue_service
        )
        notification = await self.make(service, FAILED)

        await service.requeue_notification(notification.id, send_after=None)

        assert queue_service.enqueued_notification_ids == [notification.id]
        assert (await self.backend.get_notification(notification.id)).status == PENDING

    async def test_requeues_a_one_off_notification(self):
        service = self.build_service()
        created = await service.create_one_off_notification(**_one_off_kwargs())
        await _amove_to(self.backend, created.id, FAILED)

        requeued = await service.requeue_notification(created.id, send_after=None)

        assert isinstance(requeued, OneOffNotification)
        assert (await self.backend.get_notification(created.id)).status == SENT

    async def test_writes_to_the_primary_then_to_every_additional_backend(self):
        replica_one = _new_backend(FakeAsyncIOFileBackend)
        replica_two = _new_backend(_AsyncIONoSnapshotReplica)
        self._backends += [replica_one, replica_two]
        service = self.build_service(additional_backends=[replica_one, replica_two])
        created = await service.create_notification(**_create_kwargs())
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica_two, created)
        for backend in self._backends:
            await _amove_to(backend, created.id, FAILED)
        later = _future() + datetime.timedelta(days=2)

        await service.requeue_notification(created.id, send_after=later)

        for backend in self._backends:
            stored = await backend.get_notification(created.id)
            assert stored.status == PENDING
            assert stored.send_after == later

    async def test_a_failing_replica_does_not_change_the_primary_result(self):
        replica = _new_backend(_AsyncIOFailingReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = await service.create_notification(**_create_kwargs())
        await _amove_to(self.backend, created.id, FAILED)

        with self.assertLogs(SERVICE_LOGGER, level="ERROR"):
            requeued = await service.requeue_notification(created.id, send_after=None)

        assert requeued.status == SENT
        assert (await self.backend.get_notification(created.id)).status == SENT

    async def test_logs_and_errors_name_the_notification_by_id_only(self):
        service = self.build_service()
        failed = await self.make(service, FAILED)
        sent = await self.make(service, SENT)

        with _LogCapture() as logs:
            await service.requeue_notification(failed.id, send_after=None)
            with self.assertRaises(NotificationActionError) as raised:
                await service.requeue_notification(sent.id)

        assert str(failed.id) in logs.text()
        _assert_no_field_values(logs.text())
        _assert_no_field_values(str(raised.exception))


class AsyncIORepinNotificationTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = _new_backend(FakeAsyncIOFileBackend)
        self._backends = [self.backend]
        FakeVersionedTemplateRenderer.latest_version = 7

    async def asyncTearDown(self):
        for backend in self._backends:
            await backend.clear()
        FakeVersionedTemplateRenderer.latest_version = 7

    def build_service(
        self, renderer: str = VERSIONED_RENDERER, **kwargs: Any
    ) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [(ASYNCIO_ADAPTER, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return AsyncIONotificationService(**kwargs)

    async def make(self, service: AsyncIONotificationService, status: str, **overrides: Any):
        notification = await service.create_notification(**_create_kwargs(**overrides))
        await _amove_to(self.backend, notification.id, status)
        return await self.backend.get_notification(notification.id)

    async def test_pins_the_given_version_in_each_allowed_status(self):
        for status in REPINNABLE_NOTIFICATION_STATUSES:
            with self.subTest(status=status):
                service = self.build_service()
                notification = await self.make(service, status)

                repinned = await service.repin_notification(notification.id, template_version=3)

                assert repinned.requested_template_version == 3
                stored = await self.backend.get_notification(notification.id)
                assert stored.requested_template_version == 3
                assert stored.status == status

    async def test_refuses_a_delivered_notification_before_writing(self):
        for status in (SENT, READ):
            with self.subTest(status=status):
                service = self.build_service()
                notification = await self.make(service, status)

                with patch.object(self.backend, "persist_notification_update") as update:
                    with self.assertRaises(NotificationActionError) as raised:
                        await service.repin_notification(notification.id, template_version=3)

                update.assert_not_called()
                assert raised.exception.reason == "INVALID_STATUS"
                assert raised.exception.status == status

    async def test_pins_the_current_version_when_none_is_given(self):
        service = self.build_service()
        notification = await self.make(service, FAILED, requested_template_version=2)
        FakeVersionedTemplateRenderer.latest_version = 9

        repinned = await service.repin_notification(notification.id)

        assert repinned.requested_template_version == 9

    async def test_version_zero_is_a_version(self):
        service = self.build_service()
        notification = await self.make(service, PENDING)

        repinned = await service.repin_notification(notification.id, template_version=0)

        assert repinned.requested_template_version == 0

    async def test_repinning_to_the_pinned_version_writes_nothing(self):
        service = self.build_service()
        notification = await self.make(service, FAILED, requested_template_version=7)

        with patch.object(self.backend, "persist_notification_update") as update:
            repinned = await service.repin_notification(notification.id)

        update.assert_not_called()
        assert repinned.requested_template_version == 7

    async def test_refuses_when_the_renderer_reports_no_version(self):
        service = self.build_service(renderer=PLAIN_RENDERER)
        notification = await self.make(service, FAILED)

        with self.assertRaises(NotificationActionError) as raised:
            await service.repin_notification(notification.id)

        assert raised.exception.reason == "NO_TEMPLATE_VERSION"

    async def test_a_renderer_that_raises_is_logged_and_counts_as_no_version(self):
        service = self.build_service(renderer=RAISING_RENDERER)
        notification = await self.make(service, FAILED)

        with _LogCapture() as logs:
            with self.assertRaises(NotificationActionError) as raised:
                await service.repin_notification(notification.id)

        assert raised.exception.reason == "NO_TEMPLATE_VERSION"
        assert str(notification.id) in str(raised.exception)
        assert "RuntimeError" in logs.text()
        assert "unreachable" not in logs.text()
        _assert_no_field_values(logs.text())

    async def test_an_invalid_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        for template_version in (-1, 1.5, "3", True):
            with self.subTest(template_version=template_version):
                with patch.object(self.backend, "get_notification") as get_notification:
                    with self.assertRaises(InvalidTemplateVersionError):
                        await service.repin_notification(
                            "any",
                            template_version=template_version,  # type: ignore[arg-type]
                        )

                get_notification.assert_not_called()

    async def test_unknown_id_is_not_found(self):
        service = self.build_service()

        with self.assertRaises(NotificationActionError) as raised:
            await service.repin_notification("missing", template_version=1)

        assert raised.exception.reason == "NOT_FOUND"

    async def test_repins_a_one_off_notification(self):
        service = self.build_service()
        created = await service.create_one_off_notification(**_one_off_kwargs())

        repinned = await service.repin_notification(created.id, template_version=2)

        assert isinstance(repinned, OneOffNotification)
        assert repinned.requested_template_version == 2

    async def test_writes_to_every_backend_including_one_that_declines_snapshots(self):
        replica_one = _new_backend(FakeAsyncIOFileBackend)
        replica_two = _new_backend(_AsyncIONoSnapshotReplica)
        self._backends += [replica_one, replica_two]
        service = self.build_service(additional_backends=[replica_one, replica_two])
        created = await service.create_notification(**_create_kwargs())
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica_two, created)

        await service.repin_notification(created.id, template_version=4)

        for backend in self._backends:
            stored = await backend.get_notification(created.id)
            assert stored.requested_template_version == 4

    async def test_a_failing_replica_does_not_change_the_primary_result(self):
        replica = _new_backend(_AsyncIOFailingReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = await service.create_notification(**_create_kwargs())

        with self.assertLogs(SERVICE_LOGGER, level="ERROR"):
            repinned = await service.repin_notification(created.id, template_version=4)

        assert repinned.requested_template_version == 4

    async def test_update_notification_does_not_send_a_failed_notification(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        await service.update_notification(notification.id, send_after=None)

        assert (await self.backend.get_notification(notification.id)).status == FAILED
        assert _sent(service) == []  # type: ignore[union-attr]


# --- the fake backends' own requeue_notification -----------------------------------------


def _persist_kwargs() -> dict[str, Any]:
    kwargs = _create_kwargs()
    kwargs["attachments"] = [NotificationAttachment(file=b"report", filename="report.txt")]
    return kwargs


class FakeBackendRequeueTestCase(TestCase):
    def setUp(self):
        self.backend = _new_backend(FakeFileBackend)
        self.backend.inject_attachment_manager(FakeAttachmentManager())

    def tearDown(self):
        self.backend.clear()

    def make(self, status: str) -> Notification:
        notification = self.backend.persist_notification(**_persist_kwargs())
        _move_to(self.backend, notification.id, status)
        return notification

    def test_moves_only_a_requeueable_status(self):
        for status in ALL_STATUSES:
            with self.subTest(status=status):
                notification = self.make(status)
                if status in REQUEUEABLE_NOTIFICATION_STATUSES:
                    assert self.backend.requeue_notification(notification.id).status == PENDING
                    continue
                with self.assertRaises(NotificationRequeueError) as raised:
                    self.backend.requeue_notification(notification.id)
                assert str(raised.exception) == (
                    f"Cannot requeue notification {notification.id}: it is not FAILED or CANCELLED"
                )
                assert self.backend.get_notification(notification.id).status == status

    def test_an_unknown_id_is_not_found(self):
        with self.assertRaises(NotificationNotFoundError):
            self.backend.requeue_notification(uuid.uuid4())

    def test_send_after_is_applied_only_when_passed(self):
        notification = self.make(FAILED)
        original = notification.send_after
        later = _future() + datetime.timedelta(days=5)

        assert self.backend.requeue_notification(notification.id).send_after == original
        self.backend.mark_pending_as_failed(notification.id)
        assert self.backend.requeue_notification(notification.id, later).send_after == later
        self.backend.mark_pending_as_failed(notification.id)
        assert self.backend.requeue_notification(notification.id, None).send_after is None

    def test_clears_sent_at(self):
        notification = self.make(FAILED)
        self.backend.get_notification(notification.id).sent_at = _now()

        assert self.backend.requeue_notification(notification.id).sent_at is None

    def test_returns_the_attachments(self):
        notification = self.make(CANCELLED)

        requeued = self.backend.requeue_notification(notification.id)

        assert [attachment.filename for attachment in requeued.attachments] == ["report.txt"]

    def test_cancel_keeps_the_row_and_only_cancels_a_pending_notification(self):
        notification = self.make(PENDING)

        self.backend.cancel_notification(notification.id)

        assert self.backend.get_notification(notification.id).status == CANCELLED
        with self.assertRaises(NotificationCancelError):
            self.backend.cancel_notification(notification.id)


class FakeAsyncIOBackendRequeueTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = _new_backend(FakeAsyncIOFileBackend)
        self.backend.inject_attachment_manager(FakeAsyncIOAttachmentManager())

    async def asyncTearDown(self):
        await self.backend.clear()

    async def make(self, status: str) -> Notification:
        notification = await self.backend.persist_notification(**_persist_kwargs())
        await _amove_to(self.backend, notification.id, status)
        return notification

    async def test_moves_only_a_requeueable_status(self):
        for status in ALL_STATUSES:
            with self.subTest(status=status):
                notification = await self.make(status)
                if status in REQUEUEABLE_NOTIFICATION_STATUSES:
                    requeued = await self.backend.requeue_notification(notification.id)
                    assert requeued.status == PENDING
                    continue
                with self.assertRaises(NotificationRequeueError) as raised:
                    await self.backend.requeue_notification(notification.id)
                assert str(raised.exception) == (
                    f"Cannot requeue notification {notification.id}: it is not FAILED or CANCELLED"
                )
                assert (await self.backend.get_notification(notification.id)).status == status

    async def test_an_unknown_id_is_not_found(self):
        with self.assertRaises(NotificationNotFoundError):
            await self.backend.requeue_notification(uuid.uuid4())

    async def test_send_after_is_applied_only_when_passed(self):
        notification = await self.make(FAILED)
        original = notification.send_after
        later = _future() + datetime.timedelta(days=5)

        assert (await self.backend.requeue_notification(notification.id)).send_after == original
        await self.backend.mark_pending_as_failed(notification.id)
        requeued = await self.backend.requeue_notification(notification.id, later)
        assert requeued.send_after == later
        await self.backend.mark_pending_as_failed(notification.id)
        requeued = await self.backend.requeue_notification(notification.id, None)
        assert requeued.send_after is None

    async def test_clears_sent_at(self):
        notification = await self.make(FAILED)
        (await self.backend.get_notification(notification.id)).sent_at = _now()

        assert (await self.backend.requeue_notification(notification.id)).sent_at is None

    async def test_returns_the_attachments(self):
        notification = await self.make(CANCELLED)

        requeued = await self.backend.requeue_notification(notification.id)

        assert [attachment.filename for attachment in requeued.attachments] == ["report.txt"]

    async def test_cancel_keeps_the_row_and_only_cancels_a_pending_notification(self):
        notification = await self.make(PENDING)

        await self.backend.cancel_notification(notification.id)

        assert (await self.backend.get_notification(notification.id)).status == CANCELLED
        with self.assertRaises(NotificationCancelError):
            await self.backend.cancel_notification(notification.id)
