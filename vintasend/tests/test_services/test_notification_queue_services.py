"""Unit tests for the queue-service seam: the ABCs, their fakes, and the resolver helpers."""

from abc import ABC
from unittest import IsolatedAsyncioTestCase, TestCase

import pytest

from vintasend.exceptions import NotificationQueueServiceMissingError
from vintasend.services.helpers import (
    get_asyncio_notification_queue_service,
    get_notification_queue_service,
)
from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService
from vintasend.services.notification_queue_services.stubs.fake_queue_service import (
    FakeAsyncIOQueueService,
    FakeQueueService,
)


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


class GetNotificationQueueServiceTestCase(TestCase):
    def test_resolves_a_valid_import_string(self):
        queue_service = get_notification_queue_service(
            "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeQueueService"
        )

        assert isinstance(queue_service, FakeQueueService)

    def test_raises_typed_error_on_a_bad_import_string(self):
        with pytest.raises(NotificationQueueServiceMissingError):
            get_notification_queue_service("vintasend.does.not.exist.NotARealQueueService")

    def test_raises_typed_error_when_resolved_class_is_not_a_queue_service(self):
        with pytest.raises(NotificationQueueServiceMissingError):
            get_notification_queue_service("builtins.object")


class GetAsyncioNotificationQueueServiceTestCase(TestCase):
    def test_resolves_a_valid_import_string(self):
        queue_service = get_asyncio_notification_queue_service(
            "vintasend.services.notification_queue_services.stubs.fake_queue_service.FakeAsyncIOQueueService"
        )

        assert isinstance(queue_service, FakeAsyncIOQueueService)

    def test_raises_typed_error_on_a_bad_import_string(self):
        with pytest.raises(NotificationQueueServiceMissingError):
            get_asyncio_notification_queue_service("vintasend.does.not.exist.NotARealQueueService")

    def test_raises_typed_error_when_resolved_class_is_not_a_queue_service(self):
        with pytest.raises(NotificationQueueServiceMissingError):
            get_asyncio_notification_queue_service("builtins.object")
