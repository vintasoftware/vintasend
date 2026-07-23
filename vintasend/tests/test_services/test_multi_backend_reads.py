import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase

import pytest

from vintasend.constants import NotificationTypes
from vintasend.exceptions import BackendNotFoundError, NotificationNotFoundError
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


IN_APP_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
)

ASYNCIO_IN_APP_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeAsyncIOInAppAdapter",
    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
)


class BackendWithCustomIdentifier(FakeFileBackend):
    """A backend that declares its own stable identifier instead of the service fallback."""

    def get_backend_identifier(self) -> str | None:
        return "custom-primary"


class AsyncIOBackendWithCustomIdentifier(FakeAsyncIOFileBackend):
    """AsyncIO twin of ``BackendWithCustomIdentifier``."""

    def get_backend_identifier(self) -> str | None:
        return "custom-primary"


def _build_context(test):
    if test != "test":
        raise ValueError()
    return NotificationContextDict({"test": "test"})


class MultiBackendReadRoutingTestCase(TestCase):
    """Sync ``NotificationService`` backend registry and read routing (Phase 1)."""

    def setUp(self):
        register_context("multi_backend_reads_test_context")(_build_context)
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

    def _create_via_backend(self, backend: FakeFileBackend, title: str = "Notification"):
        return backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_reads_test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    # --- registry: identifiers -------------------------------------------------

    def test_reports_ordered_identifiers_for_primary_and_additional_backends(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        assert service.get_all_backend_identifiers() == ["backend-0", "backend-1", "backend-2"]
        assert service.get_primary_backend_identifier() == "backend-0"
        assert service.get_additional_backend_identifiers() == ["backend-1", "backend-2"]

    def test_custom_identifier_used_when_declared_fallback_otherwise(self):
        custom_backend = BackendWithCustomIdentifier(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(custom_backend)

        service = self.build_service(additional_backends=[self.replica_one, custom_backend])

        # replica_one declares no identifier -> falls back to its position (backend-1);
        # custom_backend declares "custom-primary" and keeps it despite being 2nd.
        assert service.get_all_backend_identifiers() == [
            "backend-0",
            "backend-1",
            "custom-primary",
        ]
        assert service.has_backend("custom-primary")

    def test_additional_backend_given_as_import_string_is_resolved(self):
        service = self.build_service(
            additional_backends=[
                "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend"
            ]
        )
        # Resolved with no kwargs (the additional_backends signature carries no per-backend
        # kwargs), so it constructed with the default database file name; nothing is
        # written to it here since this test only inspects the registry.
        assert service.get_all_backend_identifiers() == ["backend-0", "backend-1"]
        assert service.has_backend("backend-1") is True

    def test_has_backend_reports_accurately(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        assert service.has_backend("backend-0") is True
        assert service.has_backend("backend-1") is True
        assert service.has_backend("backend-2") is True
        assert service.has_backend("backend-3") is False

    # --- read routing ------------------------------------------------------------

    def test_read_without_backend_identifier_hits_primary(self):
        service = self.build_service(additional_backends=[self.replica_one])
        notification = self._create_via_backend(self.primary_backend, "Primary notification")

        fetched = service.get_notification(notification.id)
        assert fetched.id == notification.id

        with pytest.raises(NotificationNotFoundError):
            service.get_notification(notification.id, backend_identifier="backend-1")

    def test_read_with_replica_identifier_hits_that_replica(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        replica_notification = self._create_via_backend(self.replica_one, "Replica-only")

        fetched = service.get_notification(replica_notification.id, backend_identifier="backend-1")
        assert fetched.id == replica_notification.id

        with pytest.raises(NotificationNotFoundError):
            service.get_notification(replica_notification.id)
        with pytest.raises(NotificationNotFoundError):
            service.get_notification(replica_notification.id, backend_identifier="backend-2")

    def test_filter_and_count_notifications_route_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])
        self._create_via_backend(self.replica_one, "Replica notification")

        assert list(service.filter_notifications({}, page=1, page_size=10)) == []
        assert service.count_notifications({}) == 0

        replica_results = list(
            service.filter_notifications({}, page=1, page_size=10, backend_identifier="backend-1")
        )
        assert len(replica_results) == 1
        assert service.count_notifications({}, backend_identifier="backend-1") == 1

    def test_get_all_future_and_pending_notifications_route_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])
        self._create_via_backend(self.replica_one, "Replica notification")

        assert list(service.get_all_future_notifications()) == []
        assert list(service.get_pending_notifications(page=1, page_size=10)) == []

        assert len(list(service.get_all_future_notifications(backend_identifier="backend-1"))) == 0
        replica_pending = list(
            service.get_pending_notifications(page=1, page_size=10, backend_identifier="backend-1")
        )
        assert len(replica_pending) == 1

    def test_get_backend_supported_filter_capabilities_routes_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])

        # Both are FakeFileBackend instances declining nothing.
        assert service.get_backend_supported_filter_capabilities() == (
            service.get_backend_supported_filter_capabilities(backend_identifier="backend-1")
        )

    def test_unknown_backend_identifier_raises_backend_not_found(self):
        service = self.build_service(additional_backends=[self.replica_one])

        with pytest.raises(BackendNotFoundError):
            service.get_notification("missing-id", backend_identifier="does-not-exist")
        with pytest.raises(BackendNotFoundError):
            list(
                service.filter_notifications(
                    {}, page=1, page_size=10, backend_identifier="does-not-exist"
                )
            )
        with pytest.raises(BackendNotFoundError):
            service.count_notifications({}, backend_identifier="does-not-exist")

    # --- backwards compatibility ---------------------------------------------------

    def test_single_backend_service_behaves_like_a_2_0_deployment(self):
        service = NotificationService(
            notification_adapters=[IN_APP_ADAPTER],
            notification_backend=self.primary_backend,
        )

        assert service.get_all_backend_identifiers() == ["backend-0"]
        assert service.get_additional_backend_identifiers() == []

        notification = self._create_via_backend(self.primary_backend, "Test")

        # Every read call site keeps working positionally, with no backend_identifier.
        fetched = service.get_notification(notification.id)
        assert fetched.id == notification.id
        assert len(list(service.filter_notifications({}, 1, 10))) == 1
        assert service.count_notifications({}) == 1


class MultiBackendBackendDefaultsTestCase(TestCase):
    """The concrete ``get_backend_identifier`` / ``get_all_notifications`` base defaults."""

    def setUp(self):
        register_context("multi_backend_reads_test_context")(_build_context)
        self.backend = FakeFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))

    def tearDown(self):
        self.backend.clear()

    def test_get_backend_identifier_default_is_none(self):
        assert self.backend.get_backend_identifier() is None

    def test_get_all_notifications_default_returns_every_notification(self):
        for i in range(3):
            self.backend.persist_notification(
                user_id=1,
                notification_type=NotificationTypes.IN_APP.value,
                title=f"Notification {i}",
                body_template="body.html",
                context_name="multi_backend_reads_test_context",
                context_kwargs={"test": "test"},
                send_after=None,
                subject_template="subject.txt",
                preheader_template="preheader.html",
            )

        all_notifications = list(self.backend.get_all_notifications())
        assert len(all_notifications) == 3
        assert {n.title for n in all_notifications} == {
            "Notification 0",
            "Notification 1",
            "Notification 2",
        }


class AsyncIOMultiBackendReadRoutingTestCase(IsolatedAsyncioTestCase):
    """AsyncIO twin of ``MultiBackendReadRoutingTestCase``."""

    def setUp(self):
        register_context("multi_backend_reads_test_context")(_build_context)
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

    async def _create_via_backend(
        self, backend: FakeAsyncIOFileBackend, title: str = "Notification"
    ):
        return await backend.persist_notification(
            user_id=1,
            notification_type=NotificationTypes.IN_APP.value,
            title=title,
            body_template="body.html",
            context_name="multi_backend_reads_test_context",
            context_kwargs={"test": "test"},
            send_after=None,
            subject_template="subject.txt",
            preheader_template="preheader.html",
        )

    # --- registry: identifiers -------------------------------------------------

    @pytest.mark.asyncio
    async def test_reports_ordered_identifiers_for_primary_and_additional_backends(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        assert service.get_all_backend_identifiers() == ["backend-0", "backend-1", "backend-2"]
        assert service.get_primary_backend_identifier() == "backend-0"
        assert service.get_additional_backend_identifiers() == ["backend-1", "backend-2"]

    @pytest.mark.asyncio
    async def test_custom_identifier_used_when_declared_fallback_otherwise(self):
        custom_backend = AsyncIOBackendWithCustomIdentifier(
            database_file_name=tempfile.mktemp(suffix=".json")
        )
        self._owned_backends.append(custom_backend)

        service = self.build_service(additional_backends=[self.replica_one, custom_backend])

        assert service.get_all_backend_identifiers() == [
            "backend-0",
            "backend-1",
            "custom-primary",
        ]
        assert service.has_backend("custom-primary")

    @pytest.mark.asyncio
    async def test_additional_backend_given_as_import_string_is_resolved(self):
        service = self.build_service(
            additional_backends=[
                "vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend"
            ]
        )
        # Resolved with no kwargs (the additional_backends signature carries no per-backend
        # kwargs), so it constructed with the default database file name; nothing is
        # written to it here since this test only inspects the registry.
        assert service.get_all_backend_identifiers() == ["backend-0", "backend-1"]
        assert service.has_backend("backend-1") is True

    @pytest.mark.asyncio
    async def test_has_backend_reports_accurately(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])

        assert service.has_backend("backend-0") is True
        assert service.has_backend("backend-1") is True
        assert service.has_backend("backend-2") is True
        assert service.has_backend("backend-3") is False

    # --- read routing ------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_read_without_backend_identifier_hits_primary(self):
        service = self.build_service(additional_backends=[self.replica_one])
        notification = await self._create_via_backend(self.primary_backend, "Primary notification")

        fetched = await service.get_notification(notification.id)
        assert fetched.id == notification.id

        with pytest.raises(NotificationNotFoundError):
            await service.get_notification(notification.id, backend_identifier="backend-1")

    @pytest.mark.asyncio
    async def test_read_with_replica_identifier_hits_that_replica(self):
        service = self.build_service(additional_backends=[self.replica_one, self.replica_two])
        replica_notification = await self._create_via_backend(self.replica_one, "Replica-only")

        fetched = await service.get_notification(
            replica_notification.id, backend_identifier="backend-1"
        )
        assert fetched.id == replica_notification.id

        with pytest.raises(NotificationNotFoundError):
            await service.get_notification(replica_notification.id)
        with pytest.raises(NotificationNotFoundError):
            await service.get_notification(replica_notification.id, backend_identifier="backend-2")

    @pytest.mark.asyncio
    async def test_filter_and_count_notifications_route_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])
        await self._create_via_backend(self.replica_one, "Replica notification")

        assert list(await service.filter_notifications({}, page=1, page_size=10)) == []
        assert await service.count_notifications({}) == 0

        replica_results = list(
            await service.filter_notifications(
                {}, page=1, page_size=10, backend_identifier="backend-1"
            )
        )
        assert len(replica_results) == 1
        assert await service.count_notifications({}, backend_identifier="backend-1") == 1

    @pytest.mark.asyncio
    async def test_get_all_future_and_pending_notifications_route_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])
        await self._create_via_backend(self.replica_one, "Replica notification")

        assert list(await service.get_all_future_notifications()) == []
        assert list(await service.get_pending_notifications(page=1, page_size=10)) == []

        assert (
            len(list(await service.get_all_future_notifications(backend_identifier="backend-1")))
            == 0
        )
        replica_pending = list(
            await service.get_pending_notifications(
                page=1, page_size=10, backend_identifier="backend-1"
            )
        )
        assert len(replica_pending) == 1

    @pytest.mark.asyncio
    async def test_get_backend_supported_filter_capabilities_routes_to_named_backend(self):
        service = self.build_service(additional_backends=[self.replica_one])

        assert (await service.get_backend_supported_filter_capabilities()) == (
            await service.get_backend_supported_filter_capabilities(backend_identifier="backend-1")
        )

    @pytest.mark.asyncio
    async def test_unknown_backend_identifier_raises_backend_not_found(self):
        service = self.build_service(additional_backends=[self.replica_one])

        with pytest.raises(BackendNotFoundError):
            await service.get_notification("missing-id", backend_identifier="does-not-exist")
        with pytest.raises(BackendNotFoundError):
            list(
                await service.filter_notifications(
                    {}, page=1, page_size=10, backend_identifier="does-not-exist"
                )
            )
        with pytest.raises(BackendNotFoundError):
            await service.count_notifications({}, backend_identifier="does-not-exist")

    # --- backwards compatibility ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_single_backend_service_behaves_like_a_2_0_deployment(self):
        service = AsyncIONotificationService(
            notification_adapters=[ASYNCIO_IN_APP_ADAPTER],
            notification_backend=self.primary_backend,
        )

        assert service.get_all_backend_identifiers() == ["backend-0"]
        assert service.get_additional_backend_identifiers() == []

        notification = await self._create_via_backend(self.primary_backend, "Test")

        fetched = await service.get_notification(notification.id)
        assert fetched.id == notification.id
        assert len(list(await service.filter_notifications({}, 1, 10))) == 1
        assert await service.count_notifications({}) == 1


class AsyncIOMultiBackendBackendDefaultsTestCase(IsolatedAsyncioTestCase):
    """AsyncIO twin of ``MultiBackendBackendDefaultsTestCase``."""

    def setUp(self):
        register_context("multi_backend_reads_test_context")(_build_context)
        self.backend = FakeAsyncIOFileBackend(database_file_name=tempfile.mktemp(suffix=".json"))

    def tearDown(self):
        FakeFileBackend(database_file_name=self.backend.database_file_name).clear()

    @pytest.mark.asyncio
    async def test_get_backend_identifier_default_is_none(self):
        assert self.backend.get_backend_identifier() is None

    @pytest.mark.asyncio
    async def test_get_all_notifications_default_returns_every_notification(self):
        for i in range(3):
            await self.backend.persist_notification(
                user_id=1,
                notification_type=NotificationTypes.IN_APP.value,
                title=f"Notification {i}",
                body_template="body.html",
                context_name="multi_backend_reads_test_context",
                context_kwargs={"test": "test"},
                send_after=None,
                subject_template="subject.txt",
                preheader_template="preheader.html",
            )

        all_notifications = list(await self.backend.get_all_notifications())
        assert len(all_notifications) == 3
        assert {n.title for n in all_notifications} == {
            "Notification 0",
            "Notification 1",
            "Notification 2",
        }
