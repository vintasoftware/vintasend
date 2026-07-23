import datetime
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.constants import NotificationTypes
from vintasend.exceptions import BackendMigrationError, BackendNotFoundError
from vintasend.services.dataclasses import NotificationContextDict
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
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


# --- test-only backends that exercise the per-record failure branch -------------------------


class _RaisingForIdReplica(FakeFileBackend):
    """A destination that raises for one specific notification id, succeeding for the rest.

    Mirrors ``AlwaysRaisingReplica`` in ``test_multi_backend_writes.py`` -- the raised message
    carries no duplicate/conflict marker, so the service re-raises rather than flipping to the
    read-then-write fallback, and ``migrate_to_backend`` must capture it as a per-record failure
    without aborting the rest of the migration.
    """

    failing_notification_id: object = None

    def apply_replication_snapshot_if_newer(self, snapshot):
        if self.failing_notification_id is not None and str(snapshot.id) == str(
            self.failing_notification_id
        ):
            raise RuntimeError("destination write failed for this record")
        return super().apply_replication_snapshot_if_newer(snapshot)


class _AsyncIORaisingForIdReplica(FakeAsyncIOFileBackend):
    """AsyncIO twin of ``_RaisingForIdReplica``."""

    failing_notification_id: object = None

    async def apply_replication_snapshot_if_newer(self, snapshot):
        if self.failing_notification_id is not None and str(snapshot.id) == str(
            self.failing_notification_id
        ):
            raise RuntimeError("destination write failed for this record")
        return await super().apply_replication_snapshot_if_newer(snapshot)


# --- sync: migrate_to_backend ------------------------------------------------------------------


class BackendMigrationTestCase(TestCase):
    """Sync ``NotificationService.migrate_to_backend`` (Phase 5)."""

    def setUp(self):
        register_context("backend_migration_test_context")(_build_context)
        self.primary_backend = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self.replica_one = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends = [self.primary_backend, self.replica_one]

    def tearDown(self):
        for backend in self._owned_backends:
            backend.clear()

    def build_service(self, **kwargs) -> NotificationService:
        kwargs.setdefault("notification_adapters", [IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return NotificationService(**kwargs)

    def _create(self, service: NotificationService, title: str = "Notification"):
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="backend_migration_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    def test_migration_copies_all_records_across_pages(self):
        # Populate the primary only -- no additional backends configured yet, so the replica
        # starts empty and every record must actually travel through migrate_to_backend.
        primary_only = self.build_service()
        notifications = [self._create(primary_only, title=f"n{i}") for i in range(5)]

        worker = self.build_service(additional_backends=[self.replica_one])
        # batch_size=2 against 5 records forces three pages (2, 2, 1).
        result = worker.migrate_to_backend("backend-1", batch_size=2)

        assert result["migrated"] == 5
        assert result["failures"] == []
        for notification in notifications:
            replicated = self.replica_one.get_notification(notification.id)
            assert replicated.title == notification.title

    def test_migration_is_idempotent_on_rerun(self):
        primary_only = self.build_service()
        notifications = [self._create(primary_only, title=f"n{i}") for i in range(5)]

        worker = self.build_service(additional_backends=[self.replica_one])
        first_result = worker.migrate_to_backend("backend-1", batch_size=2)
        second_result = worker.migrate_to_backend("backend-1", batch_size=2)

        assert first_result == {"migrated": 5, "failures": []}
        assert second_result == {"migrated": 5, "failures": []}
        # No duplicates: the replica holds exactly the source's records, once each.
        assert self.replica_one.count_notifications({}) == 5
        assert sorted(str(n.id) for n in self.replica_one.get_all_notifications()) == sorted(
            str(n.id) for n in notifications
        )

    def test_destination_write_failure_reported_without_aborting_migration(self):
        primary_only = self.build_service()
        notifications = [self._create(primary_only, title=f"n{i}") for i in range(3)]
        failing_notification = notifications[1]

        destination = _RaisingForIdReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        destination.failing_notification_id = failing_notification.id
        self._owned_backends.append(destination)

        worker = self.build_service(additional_backends=[destination])
        result = worker.migrate_to_backend("backend-1", batch_size=10)

        assert result["migrated"] == 2
        assert result["failures"] == [
            {
                "notification_id": failing_notification.id,
                "error": "destination write failed for this record",
            }
        ]
        for notification in notifications:
            if notification.id == failing_notification.id:
                with self.assertRaises(Exception):  # noqa: B017
                    destination.get_notification(notification.id)
            else:
                assert destination.get_notification(notification.id).id == notification.id

    def test_declining_destination_without_the_row_is_reported_as_a_failure(self):
        primary_only = self.build_service()
        notification = self._create(primary_only)

        declining = NoSnapshotApplyReplica(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(declining)

        worker = self.build_service(additional_backends=[declining])
        result = worker.migrate_to_backend("backend-1", batch_size=10)

        assert result["migrated"] == 0
        assert result["failures"] == [
            {
                "notification_id": notification.id,
                "error": (
                    "destination lacks apply_replication_snapshot_if_newer and could not be "
                    "populated with the primary id"
                ),
            }
        ]

    def test_unknown_source_raises_backend_not_found(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            worker.migrate_to_backend(
                "backend-1", batch_size=10, source_backend_identifier="no-such-backend"
            )

    def test_unknown_destination_raises_backend_not_found(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            worker.migrate_to_backend("no-such-backend", batch_size=10)

    def test_source_equal_to_destination_raises_backend_migration_error(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        # Default source (primary, "backend-0") migrated onto itself.
        with self.assertRaises(BackendMigrationError):
            worker.migrate_to_backend("backend-0", batch_size=10)
        # An explicit source equal to the destination is the same mistake.
        with self.assertRaises(BackendMigrationError):
            worker.migrate_to_backend(
                "backend-1", batch_size=10, source_backend_identifier="backend-1"
            )

    def test_migrate_to_empty_replica_matches_primary_and_second_run_changes_nothing(self):
        # Integration: populate the primary, migrate to an empty replica, assert it matches; a
        # second run must change nothing.
        primary_only = self.build_service()
        notifications = [self._create(primary_only, title=f"n{i}") for i in range(4)]

        worker = self.build_service(additional_backends=[self.replica_one])
        first_result = worker.migrate_to_backend("backend-1", batch_size=3)

        assert first_result == {"migrated": 4, "failures": []}
        primary_ids = sorted(
            str(n.id) for n in primary_only.notification_backend.get_all_notifications()
        )
        replica_ids = sorted(str(n.id) for n in self.replica_one.get_all_notifications())
        assert primary_ids == replica_ids
        for notification in notifications:
            assert self.replica_one.get_notification(notification.id).title == notification.title

        second_result = worker.migrate_to_backend("backend-1", batch_size=3)

        assert second_result == {"migrated": 4, "failures": []}
        assert self.replica_one.count_notifications({}) == 4


# --- asyncio: migrate_to_backend ---------------------------------------------------------------


class AsyncIOBackendMigrationTestCase(IsolatedAsyncioTestCase):
    """AsyncIO twin of ``BackendMigrationTestCase``."""

    def setUp(self):
        register_context("backend_migration_test_context")(_build_context)
        self.primary_backend = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_one = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends = [self.primary_backend, self.replica_one]

    async def asyncTearDown(self):
        for backend in self._owned_backends:
            await backend.clear()

    def build_service(self, **kwargs) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [ASYNCIO_IN_APP_ADAPTER])
        kwargs.setdefault("notification_backend", self.primary_backend)
        return AsyncIONotificationService(**kwargs)

    async def _create(self, service: AsyncIONotificationService, title: str = "Notification"):
        return await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="backend_migration_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    async def test_migration_copies_all_records_across_pages(self):
        primary_only = self.build_service()
        notifications = [await self._create(primary_only, title=f"n{i}") for i in range(5)]

        worker = self.build_service(additional_backends=[self.replica_one])
        result = await worker.migrate_to_backend("backend-1", batch_size=2)

        assert result["migrated"] == 5
        assert result["failures"] == []
        for notification in notifications:
            replicated = await self.replica_one.get_notification(notification.id)
            assert replicated.title == notification.title

    async def test_migration_is_idempotent_on_rerun(self):
        primary_only = self.build_service()
        notifications = [await self._create(primary_only, title=f"n{i}") for i in range(5)]

        worker = self.build_service(additional_backends=[self.replica_one])
        first_result = await worker.migrate_to_backend("backend-1", batch_size=2)
        second_result = await worker.migrate_to_backend("backend-1", batch_size=2)

        assert first_result == {"migrated": 5, "failures": []}
        assert second_result == {"migrated": 5, "failures": []}
        assert await self.replica_one.count_notifications({}) == 5
        replica_all = await self.replica_one.get_all_notifications()
        assert sorted(str(n.id) for n in replica_all) == sorted(str(n.id) for n in notifications)

    async def test_destination_write_failure_reported_without_aborting_migration(self):
        primary_only = self.build_service()
        notifications = [await self._create(primary_only, title=f"n{i}") for i in range(3)]
        failing_notification = notifications[1]

        destination = _AsyncIORaisingForIdReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        destination.failing_notification_id = failing_notification.id
        self._owned_backends.append(destination)

        worker = self.build_service(additional_backends=[destination])
        result = await worker.migrate_to_backend("backend-1", batch_size=10)

        assert result["migrated"] == 2
        assert result["failures"] == [
            {
                "notification_id": failing_notification.id,
                "error": "destination write failed for this record",
            }
        ]
        for notification in notifications:
            if notification.id == failing_notification.id:
                with self.assertRaises(Exception):  # noqa: B017
                    await destination.get_notification(notification.id)
            else:
                assert (await destination.get_notification(notification.id)).id == notification.id

    async def test_declining_destination_without_the_row_is_reported_as_a_failure(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only)

        declining = AsyncIONoSnapshotApplyReplica(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(declining)

        worker = self.build_service(additional_backends=[declining])
        result = await worker.migrate_to_backend("backend-1", batch_size=10)

        assert result["migrated"] == 0
        assert result["failures"] == [
            {
                "notification_id": notification.id,
                "error": (
                    "destination lacks apply_replication_snapshot_if_newer and could not be "
                    "populated with the primary id"
                ),
            }
        ]

    async def test_unknown_source_raises_backend_not_found(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            await worker.migrate_to_backend(
                "backend-1", batch_size=10, source_backend_identifier="no-such-backend"
            )

    async def test_unknown_destination_raises_backend_not_found(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendNotFoundError):
            await worker.migrate_to_backend("no-such-backend", batch_size=10)

    async def test_source_equal_to_destination_raises_backend_migration_error(self):
        worker = self.build_service(additional_backends=[self.replica_one])
        with self.assertRaises(BackendMigrationError):
            await worker.migrate_to_backend("backend-0", batch_size=10)
        with self.assertRaises(BackendMigrationError):
            await worker.migrate_to_backend(
                "backend-1", batch_size=10, source_backend_identifier="backend-1"
            )

    async def test_migrate_to_empty_replica_matches_primary_and_second_run_changes_nothing(self):
        primary_only = self.build_service()
        notifications = [await self._create(primary_only, title=f"n{i}") for i in range(4)]

        worker = self.build_service(additional_backends=[self.replica_one])
        first_result = await worker.migrate_to_backend("backend-1", batch_size=3)

        assert first_result == {"migrated": 4, "failures": []}
        primary_all = await primary_only.notification_backend.get_all_notifications()
        replica_all = await self.replica_one.get_all_notifications()
        assert sorted(str(n.id) for n in primary_all) == sorted(str(n.id) for n in replica_all)
        for notification in notifications:
            replicated = await self.replica_one.get_notification(notification.id)
            assert replicated.title == notification.title

        second_result = await worker.migrate_to_backend("backend-1", batch_size=3)

        assert second_result == {"migrated": 4, "failures": []}
        assert await self.replica_one.count_notifications({}) == 4
