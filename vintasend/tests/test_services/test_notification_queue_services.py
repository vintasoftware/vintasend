"""Unit tests for the queue-service seam: the ABCs, their fakes, and the resolver helpers."""

from abc import ABC
from unittest import IsolatedAsyncioTestCase, TestCase

import pytest

from vintasend.exceptions import (
    NotificationQueueServiceMissingError,
    NotificationQueueServiceResolutionError,
)
from vintasend.services.helpers import (
    get_asyncio_notification_queue_service,
    get_notification_queue_service,
)
from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.asyncio_replication_base import (
    AsyncIOBaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService
from vintasend.services.notification_queue_services.replication_base import (
    BaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.stubs.fake_queue_service import (
    FakeAsyncIOQueueService,
    FakeQueueService,
)
from vintasend.services.notification_queue_services.stubs.fake_replication_queue_service import (
    FakeAsyncIOReplicationQueueService,
    FakeReplicationQueueService,
)
from vintasend.tests.utils import _reset_notification_settings_singleton


class BaseNotificationQueueServiceTestCase(TestCase):
    def test_is_abstract(self):
        assert issubclass(BaseNotificationQueueService, ABC)

    def test_rejects_instantiation_when_enqueue_notification_is_unimplemented(self):
        class IncompleteQueueService(BaseNotificationQueueService):
            pass

        with pytest.raises(TypeError):
            IncompleteQueueService()  # type: ignore[abstract]

    def test_allows_instantiation_when_enqueue_notification_is_implemented(self):
        class CompleteQueueService(BaseNotificationQueueService):
            def enqueue_notification(self, notification_id):
                self.notification_id = notification_id

        service = CompleteQueueService()
        service.enqueue_notification("abc")
        assert service.notification_id == "abc"


class AsyncIOBaseNotificationQueueServiceTestCase(IsolatedAsyncioTestCase):
    def test_is_abstract(self):
        assert issubclass(AsyncIOBaseNotificationQueueService, ABC)

    def test_rejects_instantiation_when_enqueue_notification_is_unimplemented(self):
        class IncompleteQueueService(AsyncIOBaseNotificationQueueService):
            pass

        with pytest.raises(TypeError):
            IncompleteQueueService()  # type: ignore[abstract]

    async def test_allows_instantiation_when_enqueue_notification_is_implemented(self):
        class CompleteQueueService(AsyncIOBaseNotificationQueueService):
            async def enqueue_notification(self, notification_id):
                self.notification_id = notification_id

        service = CompleteQueueService()
        await service.enqueue_notification("abc")
        assert service.notification_id == "abc"


class FakeQueueServiceTestCase(TestCase):
    def test_enqueue_notification_records_the_id(self):
        service = FakeQueueService()

        service.enqueue_notification("abc")

        assert service.enqueued_notification_ids == ["abc"]

    def test_enqueue_notification_records_multiple_ids_in_order(self):
        service = FakeQueueService()

        service.enqueue_notification(1)
        service.enqueue_notification(2)

        assert service.enqueued_notification_ids == [1, 2]

    def test_is_a_base_notification_queue_service(self):
        assert isinstance(FakeQueueService(), BaseNotificationQueueService)


class FakeAsyncIOQueueServiceTestCase(IsolatedAsyncioTestCase):
    async def test_enqueue_notification_records_the_id(self):
        service = FakeAsyncIOQueueService()

        await service.enqueue_notification("abc")

        assert service.enqueued_notification_ids == ["abc"]

    async def test_enqueue_notification_records_multiple_ids_in_order(self):
        service = FakeAsyncIOQueueService()

        await service.enqueue_notification(1)
        await service.enqueue_notification(2)

        assert service.enqueued_notification_ids == [1, 2]

    def test_is_an_asyncio_base_notification_queue_service(self):
        assert isinstance(FakeAsyncIOQueueService(), AsyncIOBaseNotificationQueueService)


class BaseNotificationReplicationQueueServiceTestCase(TestCase):
    def test_is_abstract(self):
        assert issubclass(BaseNotificationReplicationQueueService, ABC)

    def test_rejects_instantiation_when_enqueue_replication_is_unimplemented(self):
        class IncompleteReplicationQueueService(BaseNotificationReplicationQueueService):
            pass

        with pytest.raises(TypeError):
            IncompleteReplicationQueueService()  # type: ignore[abstract]

    def test_allows_instantiation_when_enqueue_replication_is_implemented(self):
        class CompleteReplicationQueueService(BaseNotificationReplicationQueueService):
            def enqueue_replication(self, notification_id, backend_identifier):
                self.pair = (notification_id, backend_identifier)

        service = CompleteReplicationQueueService()
        service.enqueue_replication("abc", "backend-1")
        assert service.pair == ("abc", "backend-1")


class AsyncIOBaseNotificationReplicationQueueServiceTestCase(IsolatedAsyncioTestCase):
    def test_is_abstract(self):
        assert issubclass(AsyncIOBaseNotificationReplicationQueueService, ABC)

    def test_rejects_instantiation_when_enqueue_replication_is_unimplemented(self):
        class IncompleteReplicationQueueService(AsyncIOBaseNotificationReplicationQueueService):
            pass

        with pytest.raises(TypeError):
            IncompleteReplicationQueueService()  # type: ignore[abstract]

    async def test_allows_instantiation_when_enqueue_replication_is_implemented(self):
        class CompleteReplicationQueueService(AsyncIOBaseNotificationReplicationQueueService):
            async def enqueue_replication(self, notification_id, backend_identifier):
                self.pair = (notification_id, backend_identifier)

        service = CompleteReplicationQueueService()
        await service.enqueue_replication("abc", "backend-1")
        assert service.pair == ("abc", "backend-1")


class FakeReplicationQueueServiceTestCase(TestCase):
    def test_enqueue_replication_records_the_pair(self):
        service = FakeReplicationQueueService()

        service.enqueue_replication("abc", "backend-1")

        assert service.enqueued_replications == [("abc", "backend-1")]

    def test_enqueue_replication_records_multiple_pairs_in_order(self):
        service = FakeReplicationQueueService()

        service.enqueue_replication(1, "backend-1")
        service.enqueue_replication(1, "backend-2")

        assert service.enqueued_replications == [(1, "backend-1"), (1, "backend-2")]

    def test_is_a_base_notification_replication_queue_service(self):
        assert isinstance(FakeReplicationQueueService(), BaseNotificationReplicationQueueService)


class FakeAsyncIOReplicationQueueServiceTestCase(IsolatedAsyncioTestCase):
    async def test_enqueue_replication_records_the_pair(self):
        service = FakeAsyncIOReplicationQueueService()

        await service.enqueue_replication("abc", "backend-1")

        assert service.enqueued_replications == [("abc", "backend-1")]

    async def test_enqueue_replication_records_multiple_pairs_in_order(self):
        service = FakeAsyncIOReplicationQueueService()

        await service.enqueue_replication(1, "backend-1")
        await service.enqueue_replication(1, "backend-2")

        assert service.enqueued_replications == [(1, "backend-1"), (1, "backend-2")]

    def test_is_an_asyncio_base_notification_replication_queue_service(self):
        assert isinstance(
            FakeAsyncIOReplicationQueueService(),
            AsyncIOBaseNotificationReplicationQueueService,
        )


class GetNotificationQueueServiceTestCase(TestCase):
    def test_resolves_a_valid_import_string(self):
        queue_service = get_notification_queue_service(
            "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeQueueService"
        )

        assert isinstance(queue_service, FakeQueueService)

    def test_raises_typed_error_on_a_bad_import_string(self):
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_notification_queue_service("vintasend.does.not.exist.NotARealQueueService")

    def test_raises_typed_error_when_resolved_class_is_not_a_queue_service(self):
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_notification_queue_service("builtins.object")

    def test_raises_typed_error_when_resolved_class_is_the_asyncio_queue_service(self):
        """A host wiring the AsyncIO queue service into the sync resolver is a real
        misconfiguration. This is the only assertion that pins which ABC this resolver
        checks against, since `builtins.object` alone would pass even if the resolver
        checked the wrong ABC.
        """
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_notification_queue_service(
                "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeAsyncIOQueueService"
            )

    def test_raises_typed_error_when_no_import_string_and_no_framework_detected(self):
        """Regression test: get_config() returns {} (not None) when no framework is detected.

        With no import string provided and no framework detected, NOTIFICATION_QUEUE_SERVICE
        resolves to `{}` rather than `None`, so the guard must treat any non-`str` value as
        "not configured" instead of only checking for `None`. Otherwise `_import_class({})`
        raises an uncaught AttributeError instead of the documented typed error.
        """
        _reset_notification_settings_singleton(self)

        with pytest.raises(NotificationQueueServiceMissingError):
            get_notification_queue_service(None)


class GetAsyncioNotificationQueueServiceTestCase(TestCase):
    def test_resolves_a_valid_import_string(self):
        queue_service = get_asyncio_notification_queue_service(
            "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeAsyncIOQueueService"
        )

        assert isinstance(queue_service, FakeAsyncIOQueueService)

    def test_raises_typed_error_on_a_bad_import_string(self):
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_asyncio_notification_queue_service("vintasend.does.not.exist.NotARealQueueService")

    def test_raises_typed_error_when_resolved_class_is_not_a_queue_service(self):
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_asyncio_notification_queue_service("builtins.object")

    def test_raises_typed_error_when_resolved_class_is_the_sync_queue_service(self):
        """A host wiring the sync queue service into the AsyncIO resolver is a real
        misconfiguration. This is the only assertion that pins which ABC this resolver
        checks against, since `builtins.object` alone would pass even if the resolver
        checked the wrong ABC.
        """
        with pytest.raises(NotificationQueueServiceResolutionError):
            get_asyncio_notification_queue_service(
                "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeQueueService"
            )

    def test_raises_typed_error_when_no_import_string_and_no_framework_detected(self):
        """Regression test: get_config() returns {} (not None) when no framework is detected.

        With no import string provided and no framework detected, NOTIFICATION_QUEUE_SERVICE
        resolves to `{}` rather than `None`, so the guard must treat any non-`str` value as
        "not configured" instead of only checking for `None`. Otherwise `_import_class({})`
        raises an uncaught AttributeError instead of the documented typed error.
        """
        _reset_notification_settings_singleton(self)

        with pytest.raises(NotificationQueueServiceMissingError):
            get_asyncio_notification_queue_service(None)
