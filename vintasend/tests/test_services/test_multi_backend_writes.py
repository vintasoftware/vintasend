import dataclasses
import datetime
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationNotFoundError
from vintasend.services.dataclasses import ApplyResult, NotificationContextDict
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)


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


# --- test-only backends that exercise the replica failure and fallback branches -------------


class AlwaysRaisingReplica(FakeFileBackend):
    """A replica whose snapshot application always fails with a non-conflict error.

    "replica down" carries no duplicate/conflict marker, so the service re-raises it into the
    per-backend logging branch rather than flipping -- exactly the failure-isolation path.
    """

    def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("replica down")


class AsyncIOAlwaysRaisingReplica(FakeAsyncIOFileBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("replica down")


class DuplicateOnApplyReplica(FakeFileBackend):
    """A replica that reports a unique-constraint violation from snapshot application.

    The message carries a conflict marker, so the service flips to converging the replica to
    the snapshot with primitives (the create-collides -> update reconcile).
    """

    def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("duplicate key value violates unique constraint")


class AsyncIODuplicateOnApplyReplica(FakeAsyncIOFileBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        raise RuntimeError("duplicate key value violates unique constraint")


class NoSnapshotApplyReplica(FakeFileBackend):
    """A replica that declines snapshot application, forcing the read-then-write fallback."""

    def apply_replication_snapshot_if_newer(self, snapshot):
        return ApplyResult(applied=False, reason="declined for test")


class AsyncIONoSnapshotApplyReplica(FakeAsyncIOFileBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        return ApplyResult(applied=False, reason="declined for test")


class MultiBackendWriteFanoutTestCase(TestCase):
    """Sync ``NotificationService`` inline write fan-out (Phase 2)."""

    def setUp(self):
        register_context("multi_backend_writes_test_context")(_build_context)
        self.primary_backend = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_one = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_two = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
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
            context_name="multi_backend_writes_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    # --- replication_mode --------------------------------------------------------

    def test_replication_mode_defaults_to_inline_and_is_accepted(self):
        assert self.build_service().replication_mode == "inline"
        service = self.build_service(
            additional_backends=[self.replica_one], replication_mode="queued"
        )
        assert service.replication_mode == "queued"

    # --- create fan-out ----------------------------------------------------------

    def test_create_fans_out_to_both_replicas(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        notification = self._create(service, send_after=_future())

        for replica in (self.replica_one, self.replica_two):
            replicated = replica.get_notification(notification.id)
            assert replicated.id == notification.id
            assert replicated.title == notification.title
            assert replicated.status == NotificationStatus.PENDING_SEND.value
            # A defensive copy is stored, never the primary's own record instance.
            assert replicated is not notification

    def test_immediate_create_replicates_sent_state_and_context(self):
        service = self.build_service(additional_backends=[self.replica_one])

        notification = self._create(service)

        # An immediate IN_APP send marks the row sent and stores context; every one of those
        # writes must have replicated too, leaving the replica byte-identical in state.
        primary = self.primary_backend.get_notification(notification.id)
        replicated = self.replica_one.get_notification(notification.id)
        assert primary.status == NotificationStatus.SENT.value
        assert replicated.status == NotificationStatus.SENT.value
        assert replicated.context_used == primary.context_used
        assert replicated.adapter_used == primary.adapter_used

    # --- failure isolation -------------------------------------------------------

    def test_replica_failure_does_not_fail_primary_and_is_logged(self):
        raising = AlwaysRaisingReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(raising)
        service = self.build_service(additional_backends=[raising])

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            notification = self._create(service, send_after=_future())

        # The primary write succeeded and is retrievable despite the replica rejecting it.
        primary = self.primary_backend.get_notification(notification.id)
        assert primary.id == notification.id
        # The replica never took the write, and the failure was logged, not raised.
        with self.assertRaises(NotificationNotFoundError):
            raising.get_notification(notification.id)
        assert mocked_logger.exception.called

    def test_always_raising_replica_leaves_primary_fully_usable(self):
        raising = AlwaysRaisingReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(raising)
        service = self.build_service(additional_backends=[self.replica_one, raising])

        # Immediate send exercises persist + mark_sent + store_context, each of which hits the
        # raising replica; none of them may surface as a user-visible failure.
        notification = self._create(service)

        primary = self.primary_backend.get_notification(notification.id)
        assert primary.status == NotificationStatus.SENT.value
        # The healthy replica still converged even though a sibling replica always fails.
        assert self.replica_one.get_notification(notification.id).status == (
            NotificationStatus.SENT.value
        )

    # --- duplicate-conflict flip -------------------------------------------------

    def test_duplicate_conflict_on_apply_flips_to_update_and_converges(self):
        duplicating = DuplicateOnApplyReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(duplicating)
        service = self.build_service(additional_backends=[duplicating])

        notification = self._create(service, send_after=_future(), title="original")
        # Simulate the replica already holding the row a create would collide with: seed it so
        # the flip's read-then-write converge path has a row to update.
        duplicating.notifications.append(dataclasses.replace(notification))
        duplicating._store_notifications()

        service.update_notification(notification.id, title="updated")

        # Snapshot application raised a unique-constraint error; the service flipped to
        # converging the existing replica row, which now carries the update.
        assert duplicating.get_notification(notification.id).title == "updated"

    # --- read-then-write fallback ------------------------------------------------

    def test_declining_backend_uses_read_then_write_fallback(self):
        declining = NoSnapshotApplyReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(declining)
        service = self.build_service(additional_backends=[declining])

        notification = self._create(service, send_after=_future(), title="original")
        # The declining backend could not id-preservingly create it inline, so seed the row and
        # prove a later update converges through the read-then-write fallback.
        declining.notifications.append(dataclasses.replace(notification))
        declining._store_notifications()

        service.update_notification(notification.id, title="updated")

        assert declining.get_notification(notification.id).title == "updated"

    def test_declining_backend_create_does_not_create_row_inline_and_logs_warning(self):
        """A CREATE replicated to a declining backend can't be created with the primary's id.

        Unlike the update-fallback test above, this does not pre-seed the replica row: it
        exercises the real limitation documented on ``_converge_replica_to_snapshot`` -- a
        backend that declines ``apply_replication_snapshot_if_newer`` has no way to create the
        row inline, so the create is best-effort skipped and logged for later reconciliation.
        """
        declining = NoSnapshotApplyReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(declining)
        service = self.build_service(additional_backends=[declining])

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            notification = self._create(service, send_after=_future(), title="original")

        # The primary write succeeded and holds the notification.
        primary = self.primary_backend.get_notification(notification.id)
        assert primary.id == notification.id
        # The declining replica never got the row: it cannot create it with the primary's id.
        with self.assertRaises(NotificationNotFoundError):
            declining.get_notification(notification.id)
        assert any(
            "cannot create it with its primary id inline" in message for message in captured.output
        )

    # --- mark / cancel -----------------------------------------------------------

    def test_mark_read_and_cancel_replicate(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = self._create(service)

        service.mark_read(notification.id)
        for replica in (self.replica_one, self.replica_two):
            assert replica.get_notification(notification.id).status == (
                NotificationStatus.READ.value
            )

        service.cancel_notification(notification.id)
        for replica in (self.replica_one, self.replica_two):
            with self.assertRaises(NotificationNotFoundError):
                replica.get_notification(notification.id)

    def test_update_replicates(self):
        service = self.build_service(additional_backends=[self.replica_one])
        notification = self._create(service, send_after=_future())

        service.update_notification(notification.id, title="changed")

        assert self.replica_one.get_notification(notification.id).title == "changed"

    # --- single-backend short-circuit --------------------------------------------

    def test_single_backend_takes_no_replication_path(self):
        service = self.build_service()

        with patch.object(service, "_replicate_write_to_backend") as replicate_spy:
            self._create(service)

        replicate_spy.assert_not_called()

    # --- integration: three-way convergence --------------------------------------

    def test_create_update_marksent_converges_all_backends(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        notification = self._create(service, send_after=_future(), title="v1")
        service.update_notification(notification.id, title="v2")
        service.mark_read(notification.id)

        backends = [self.primary_backend, self.replica_one, self.replica_two]
        states = [b.get_notification(notification.id) for b in backends]
        assert {s.title for s in states} == {"v2"}
        assert {s.status for s in states} == {NotificationStatus.READ.value}
        assert {str(s.id) for s in states} == {str(notification.id)}


class AsyncIOMultiBackendWriteFanoutTestCase(IsolatedAsyncioTestCase):
    """AsyncIO ``AsyncIONotificationService`` inline write fan-out (Phase 2)."""

    def setUp(self):
        register_context("multi_backend_writes_test_context")(_build_context)
        self.primary_backend = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_one = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_two = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
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
            context_name="multi_backend_writes_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=send_after,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    async def test_replication_mode_defaults_to_inline_and_is_accepted(self):
        assert self.build_service().replication_mode == "inline"
        service = self.build_service(
            additional_backends=[self.replica_one], replication_mode="queued"
        )
        assert service.replication_mode == "queued"

    async def test_create_fans_out_to_both_replicas(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        notification = await self._create(service, send_after=_future())

        for replica in (self.replica_one, self.replica_two):
            replicated = await replica.get_notification(notification.id)
            assert replicated.id == notification.id
            assert replicated.title == notification.title
            assert replicated.status == NotificationStatus.PENDING_SEND.value
            assert replicated is not notification

    async def test_immediate_create_replicates_sent_state_and_context(self):
        service = self.build_service(additional_backends=[self.replica_one])

        notification = await self._create(service)

        primary = await self.primary_backend.get_notification(notification.id)
        replicated = await self.replica_one.get_notification(notification.id)
        assert primary.status == NotificationStatus.SENT.value
        assert replicated.status == NotificationStatus.SENT.value
        assert replicated.context_used == primary.context_used
        assert replicated.adapter_used == primary.adapter_used

    async def test_replica_failure_does_not_fail_primary_and_is_logged(self):
        raising = AsyncIOAlwaysRaisingReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(raising)
        service = self.build_service(additional_backends=[raising])

        with patch("vintasend.services.notification_service.logger") as mocked_logger:
            notification = await self._create(service, send_after=_future())

        primary = await self.primary_backend.get_notification(notification.id)
        assert primary.id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            await raising.get_notification(notification.id)
        assert mocked_logger.exception.called

    async def test_always_raising_replica_leaves_primary_fully_usable(self):
        raising = AsyncIOAlwaysRaisingReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(raising)
        service = self.build_service(additional_backends=[self.replica_one, raising])

        notification = await self._create(service)

        primary = await self.primary_backend.get_notification(notification.id)
        assert primary.status == NotificationStatus.SENT.value
        assert (await self.replica_one.get_notification(notification.id)).status == (
            NotificationStatus.SENT.value
        )

    async def test_duplicate_conflict_on_apply_flips_to_update_and_converges(self):
        duplicating = AsyncIODuplicateOnApplyReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(duplicating)
        service = self.build_service(additional_backends=[duplicating])

        notification = await self._create(service, send_after=_future(), title="original")
        duplicating.notifications.append(dataclasses.replace(notification))
        await duplicating._store_notifications()

        await service.update_notification(notification.id, title="updated")

        assert (await duplicating.get_notification(notification.id)).title == "updated"

    async def test_declining_backend_uses_read_then_write_fallback(self):
        declining = AsyncIONoSnapshotApplyReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(declining)
        service = self.build_service(additional_backends=[declining])

        notification = await self._create(service, send_after=_future(), title="original")
        declining.notifications.append(dataclasses.replace(notification))
        await declining._store_notifications()

        await service.update_notification(notification.id, title="updated")

        assert (await declining.get_notification(notification.id)).title == "updated"

    async def test_declining_backend_create_does_not_create_row_inline_and_logs_warning(self):
        """A CREATE replicated to a declining backend can't be created with the primary's id.

        Unlike the update-fallback test above, this does not pre-seed the replica row: it
        exercises the real limitation documented on ``_converge_replica_to_snapshot`` -- a
        backend that declines ``apply_replication_snapshot_if_newer`` has no way to create the
        row inline, so the create is best-effort skipped and logged for later reconciliation.
        """
        declining = AsyncIONoSnapshotApplyReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(declining)
        service = self.build_service(additional_backends=[declining])

        with self.assertLogs(
            "vintasend.services.notification_service", level="WARNING"
        ) as captured:
            notification = await self._create(service, send_after=_future(), title="original")

        primary = await self.primary_backend.get_notification(notification.id)
        assert primary.id == notification.id
        with self.assertRaises(NotificationNotFoundError):
            await declining.get_notification(notification.id)
        assert any(
            "cannot create it with its primary id inline" in message for message in captured.output
        )

    async def test_mark_read_and_cancel_replicate(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = await self._create(service)

        await service.mark_read(notification.id)
        for replica in (self.replica_one, self.replica_two):
            assert (await replica.get_notification(notification.id)).status == (
                NotificationStatus.READ.value
            )

        await service.cancel_notification(notification.id)
        for replica in (self.replica_one, self.replica_two):
            with self.assertRaises(NotificationNotFoundError):
                await replica.get_notification(notification.id)

    async def test_single_backend_takes_no_replication_path(self):
        service = self.build_service()

        with patch.object(service, "_replicate_write_to_backend") as replicate_spy:
            await self._create(service)

        replicate_spy.assert_not_called()

    async def test_create_update_marksent_converges_all_backends(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        notification = await self._create(service, send_after=_future(), title="v1")
        await service.update_notification(notification.id, title="v2")
        await service.mark_read(notification.id)

        backends = [self.primary_backend, self.replica_one, self.replica_two]
        states = [await b.get_notification(notification.id) for b in backends]
        assert {s.title for s in states} == {"v2"}
        assert {s.status for s in states} == {NotificationStatus.READ.value}
        assert {str(s.id) for s in states} == {str(notification.id)}
