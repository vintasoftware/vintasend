from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService

from vintasend_implementation_template.queue_service import (
    ImplementationTemplateAsyncIOQueueService,
    ImplementationTemplateQueueService,
)


class ImplementationTemplateQueueServiceTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateQueueService, BaseNotificationQueueService)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseNotificationQueueService.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateQueueService, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateQueueService.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_enqueue(self):
        queue_service = ImplementationTemplateQueueService()
        assert isinstance(queue_service, BaseNotificationQueueService)
        with self.assertRaises(NotImplementedError):
            queue_service.enqueue_notification(notification_id=1)


class ImplementationTemplateAsyncIOQueueServiceTestCase(IsolatedAsyncioTestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(
            ImplementationTemplateAsyncIOQueueService, AsyncIOBaseNotificationQueueService
        )

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBaseNotificationQueueService.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOQueueService, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAsyncIOQueueService.__abstractmethods__ == frozenset()

    async def test_stub_is_instantiable_and_fails_loudly_on_enqueue(self):
        queue_service = ImplementationTemplateAsyncIOQueueService()
        assert isinstance(queue_service, AsyncIOBaseNotificationQueueService)
        with self.assertRaises(NotImplementedError):
            await queue_service.enqueue_notification(notification_id=1)
