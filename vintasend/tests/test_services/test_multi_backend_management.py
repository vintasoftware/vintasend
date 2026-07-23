import datetime
import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase

import pytest

from vintasend.constants import NotificationTypes
from vintasend.services.dataclasses import (
    NotificationContextDict,
    OneOffNotification,
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


# --- test-only backends that exercise the raising-backend stats branch ----------------------


class RaisingCountBackend(FakeFileBackend):
    """A backend whose ``count_notifications`` always raises.

    Used to exercise ``get_backend_sync_stats``' error-isolation: this backend must be reported
    ``status='error'`` without aborting stats for any other registered backend.
    """

    def count_notifications(self, filter):  # noqa: A002
        raise RuntimeError("backend down")


class AsyncIORaisingCountBackend(FakeAsyncIOFileBackend):
    """AsyncIO twin of ``RaisingCountBackend``."""

    async def count_notifications(self, filter):  # noqa: A002
        raise RuntimeError("backend down")


# --- sync: verify_notification_sync ----------------------------------------------------------


class VerifyNotificationSyncTestCase(TestCase):
    """Sync ``NotificationService.verify_notification_sync`` (Phase 4)."""

    def setUp(self):
        register_context("multi_backend_management_test_context")(_build_context)
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

    def _create(self, service: NotificationService, title: str = "Notification"):
        return service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_management_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    def test_fully_synced_record_reports_all_agree(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = self._create(service)

        report = service.verify_notification_sync(notification.id)

        assert report["notification_id"] == notification.id
        assert report["primary_backend_identifier"] == "backend-0"
        assert report["backends_with_record"] == ["backend-0", "backend-1", "backend-2"]
        assert report["backends_missing_record"] == []
        assert report["in_sync"] is True
        assert report["fields"]
        for field_report in report["fields"]:
            assert field_report["in_agreement"] is True
            assert field_report["differing_values"] == {}

    def test_field_differing_on_one_replica_is_flagged_with_field_and_backend(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = self._create(service, title="Original title")

        # Diverge one replica's title directly, bypassing the service's write fan-out.
        self.replica_one.persist_notification_update(
            notification_id=notification.id, update_data={"title": "Diverged title"}
        )

        report = service.verify_notification_sync(notification.id)

        assert report["in_sync"] is False
        assert report["backends_missing_record"] == []
        title_reports = [f for f in report["fields"] if f["field"] == "title"]
        assert len(title_reports) == 1
        title_report = title_reports[0]
        assert title_report["in_agreement"] is False
        assert title_report["differing_values"] == {
            "backend-0": "Original title",
            "backend-1": "Diverged title",
            "backend-2": "Original title",
        }
        # Every other comparable field still agrees.
        other_reports = [f for f in report["fields"] if f["field"] != "title"]
        assert other_reports
        for field_report in other_reports:
            assert field_report["in_agreement"] is True

    def test_record_missing_from_replica_is_reported_absent_not_error(self):
        # Create against a primary-only service, so only the primary ever gets the write.
        primary_only = self.build_service()
        notification = self._create(primary_only)

        # A monitoring service with two replicas that never received this notification.
        monitor = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        report = monitor.verify_notification_sync(notification.id)

        assert report["backends_with_record"] == ["backend-0"]
        assert report["backends_missing_record"] == ["backend-1", "backend-2"]
        assert report["in_sync"] is False

    def test_verify_notification_sync_missing_from_every_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])

        report = service.verify_notification_sync("no-such-id")

        assert report["backends_with_record"] == []
        assert report["backends_missing_record"] == ["backend-0", "backend-1"]
        assert report["in_sync"] is False
        assert report["fields"] == []

    def test_verify_notification_sync_flags_unreplicated_backend_in_two_replica_setup(self):
        # Integration: create against the primary, replicate to only ONE of two replicas via
        # process_replication, and assert the un-replicated backend is flagged.
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = self._create(service)
        # Undo the automatic inline replication to replica_two so only replica_one holds it,
        # simulating a replica that a queued replication task has not drained yet.
        service._backends["backend-2"].clear()

        report = service.verify_notification_sync(notification.id)

        assert report["backends_with_record"] == ["backend-0", "backend-1"]
        assert report["backends_missing_record"] == ["backend-2"]
        assert report["in_sync"] is False

    def test_heterogeneous_record_types_across_backends_reported_as_out_of_sync(self):
        # Pathological/corrupt-replication scenario: the same id holds a Notification on the
        # primary but a OneOffNotification on a replica. Without an explicit check this is
        # invisible to field-by-field comparison (the two types share no useful overlap) and the
        # report could wrongly claim in_sync=True.
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = self._create(service)

        self.replica_one.notifications = [
            n for n in self.replica_one.notifications if str(n.id) != str(notification.id)
        ]
        self.replica_one.notifications.append(
            OneOffNotification(
                id=notification.id,
                email_or_phone="corrupt@example.com",
                first_name="Corrupt",
                last_name="Replica",
                notification_type=NotificationTypes.IN_APP.value,
                title="Original title",
                body_template="body.html",
                context_name="multi_backend_management_test_context",
                context_kwargs={"test": "test"},
                send_after=_future(),
                subject_template="subject.txt",
                preheader_template="preheader.html",
                status=notification.status,
            )
        )

        report = service.verify_notification_sync(notification.id)

        assert report["in_sync"] is False
        record_type_reports = [f for f in report["fields"] if f["field"] == "record_type"]
        assert len(record_type_reports) == 1
        record_type_report = record_type_reports[0]
        assert record_type_report["in_agreement"] is False
        assert record_type_report["differing_values"] == {
            "backend-0": "Notification",
            "backend-1": "OneOffNotification",
            "backend-2": "Notification",
        }


# --- sync: get_backend_sync_stats ------------------------------------------------------------


class BackendSyncStatsTestCase(TestCase):
    """Sync ``NotificationService.get_backend_sync_stats`` (Phase 4)."""

    def setUp(self):
        register_context("multi_backend_management_test_context")(_build_context)
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
            context_name="multi_backend_management_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    def test_stats_report_per_backend_counts(self):
        service = self.build_service(additional_backends=[self.replica_one])
        self._create(service, title="First")
        self._create(service, title="Second")
        # An extra write directly on the replica, never seen by the primary.
        self.replica_one.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Replica-only",
            body_template="body.html",
            context_name="multi_backend_management_test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

        stats = service.get_backend_sync_stats()

        assert stats == {
            "backend-0": {"total_notifications": 2, "status": "healthy"},
            "backend-1": {"total_notifications": 3, "status": "healthy"},
        }

    def test_raising_backend_reports_error_without_propagating_stats(self):
        raising_replica = RaisingCountBackend(database_file_name=tempfile.mktemp(suffix=".json"))
        self._owned_backends.append(raising_replica)
        service = self.build_service(additional_backends=[self.replica_one, raising_replica])
        self._create(service)

        stats = service.get_backend_sync_stats()

        assert stats["backend-0"] == {"total_notifications": 1, "status": "healthy"}
        assert stats["backend-1"] == {"total_notifications": 1, "status": "healthy"}
        assert stats["backend-2"]["status"] == "error"
        assert stats["backend-2"]["total_notifications"] == 0
        assert stats["backend-2"]["error"] == "backend down"


# --- asyncio: verify_notification_sync --------------------------------------------------------


class AsyncIOVerifyNotificationSyncTestCase(IsolatedAsyncioTestCase):
    """AsyncIO twin of ``VerifyNotificationSyncTestCase``."""

    def setUp(self):
        register_context("multi_backend_management_test_context")(_build_context)
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

    def tearDown(self):
        for backend in self._owned_backends:
            FakeFileBackend(database_file_name=backend.database_file_name).clear()

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
            context_name="multi_backend_management_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    @pytest.mark.asyncio
    async def test_fully_synced_record_reports_all_agree(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = await self._create(service)

        report = await service.verify_notification_sync(notification.id)

        assert report["notification_id"] == notification.id
        assert report["primary_backend_identifier"] == "backend-0"
        assert report["backends_with_record"] == ["backend-0", "backend-1", "backend-2"]
        assert report["backends_missing_record"] == []
        assert report["in_sync"] is True
        assert report["fields"]
        for field_report in report["fields"]:
            assert field_report["in_agreement"] is True
            assert field_report["differing_values"] == {}

    @pytest.mark.asyncio
    async def test_field_differing_on_one_replica_is_flagged_with_field_and_backend(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = await self._create(service, title="Original title")

        await self.replica_one.persist_notification_update(
            notification_id=notification.id, update_data={"title": "Diverged title"}
        )

        report = await service.verify_notification_sync(notification.id)

        assert report["in_sync"] is False
        assert report["backends_missing_record"] == []
        title_reports = [f for f in report["fields"] if f["field"] == "title"]
        assert len(title_reports) == 1
        title_report = title_reports[0]
        assert title_report["in_agreement"] is False
        assert title_report["differing_values"] == {
            "backend-0": "Original title",
            "backend-1": "Diverged title",
            "backend-2": "Original title",
        }
        other_reports = [f for f in report["fields"] if f["field"] != "title"]
        assert other_reports
        for field_report in other_reports:
            assert field_report["in_agreement"] is True

    @pytest.mark.asyncio
    async def test_record_missing_from_replica_is_reported_absent_not_error(self):
        primary_only = self.build_service()
        notification = await self._create(primary_only)

        monitor = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        report = await monitor.verify_notification_sync(notification.id)

        assert report["backends_with_record"] == ["backend-0"]
        assert report["backends_missing_record"] == ["backend-1", "backend-2"]
        assert report["in_sync"] is False

    @pytest.mark.asyncio
    async def test_verify_notification_sync_missing_from_every_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])

        report = await service.verify_notification_sync("no-such-id")

        assert report["backends_with_record"] == []
        assert report["backends_missing_record"] == ["backend-0", "backend-1"]
        assert report["in_sync"] is False
        assert report["fields"] == []

    @pytest.mark.asyncio
    async def test_verify_notification_sync_flags_unreplicated_backend_in_two_replica_setup(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = await self._create(service)
        await service._backends["backend-2"].clear()

        report = await service.verify_notification_sync(notification.id)

        assert report["backends_with_record"] == ["backend-0", "backend-1"]
        assert report["backends_missing_record"] == ["backend-2"]
        assert report["in_sync"] is False

    @pytest.mark.asyncio
    async def test_heterogeneous_record_types_across_backends_reported_as_out_of_sync(self):
        # AsyncIO twin of the sync test above.
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        notification = await self._create(service)

        self.replica_one.notifications = [
            n for n in self.replica_one.notifications if str(n.id) != str(notification.id)
        ]
        self.replica_one.notifications.append(
            OneOffNotification(
                id=notification.id,
                email_or_phone="corrupt@example.com",
                first_name="Corrupt",
                last_name="Replica",
                notification_type=NotificationTypes.IN_APP.value,
                title="Original title",
                body_template="body.html",
                context_name="multi_backend_management_test_context",
                context_kwargs={"test": "test"},
                send_after=_future(),
                subject_template="subject.txt",
                preheader_template="preheader.html",
                status=notification.status,
            )
        )

        report = await service.verify_notification_sync(notification.id)

        assert report["in_sync"] is False
        record_type_reports = [f for f in report["fields"] if f["field"] == "record_type"]
        assert len(record_type_reports) == 1
        record_type_report = record_type_reports[0]
        assert record_type_report["in_agreement"] is False
        assert record_type_report["differing_values"] == {
            "backend-0": "Notification",
            "backend-1": "OneOffNotification",
            "backend-2": "Notification",
        }


# --- asyncio: get_backend_sync_stats -----------------------------------------------------------


class AsyncIOBackendSyncStatsTestCase(IsolatedAsyncioTestCase):
    """AsyncIO twin of ``BackendSyncStatsTestCase``."""

    def setUp(self):
        register_context("multi_backend_management_test_context")(_build_context)
        self.primary_backend = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self.replica_one = FakeAsyncIOFileBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends = [self.primary_backend, self.replica_one]

    def tearDown(self):
        for backend in self._owned_backends:
            FakeFileBackend(database_file_name=backend.database_file_name).clear()

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
            context_name="multi_backend_management_test_context",
            context_kwargs=NotificationContextDict({"test": "test"}),
            send_after=_future(),
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    @pytest.mark.asyncio
    async def test_stats_report_per_backend_counts(self):
        service = self.build_service(additional_backends=[self.replica_one])
        await self._create(service, title="First")
        await self._create(service, title="Second")
        await self.replica_one.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title="Replica-only",
            body_template="body.html",
            context_name="multi_backend_management_test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

        stats = await service.get_backend_sync_stats()

        assert stats == {
            "backend-0": {"total_notifications": 2, "status": "healthy"},
            "backend-1": {"total_notifications": 3, "status": "healthy"},
        }

    @pytest.mark.asyncio
    async def test_raising_backend_reports_error_without_propagating_stats(self):
        raising_replica = AsyncIORaisingCountBackend(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(raising_replica)
        service = self.build_service(additional_backends=[self.replica_one, raising_replica])
        await self._create(service)

        stats = await service.get_backend_sync_stats()

        assert stats["backend-0"] == {"total_notifications": 1, "status": "healthy"}
        assert stats["backend-1"] == {"total_notifications": 1, "status": "healthy"}
        assert stats["backend-2"]["status"] == "error"
        assert stats["backend-2"]["total_notifications"] == 0
        assert stats["backend-2"]["error"] == "backend down"
