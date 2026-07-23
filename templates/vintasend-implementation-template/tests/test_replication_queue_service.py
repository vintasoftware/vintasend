from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.services.notification_queue_services.asyncio_replication_base import (
    AsyncIOBaseNotificationReplicationQueueService,
)
from vintasend.services.notification_queue_services.replication_base import (
    BaseNotificationReplicationQueueService,
)

from vintasend_implementation_template.replication_queue_service import (
    ImplementationTemplateAsyncIOReplicationQueueService,
    ImplementationTemplateReplicationQueueService,
)


class ImplementationTemplateReplicationQueueServiceTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(
            ImplementationTemplateReplicationQueueService,
            BaseNotificationReplicationQueueService,
        )

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseNotificationReplicationQueueService.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateReplicationQueueService, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateReplicationQueueService.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_enqueue(self):
        replication_queue_service = ImplementationTemplateReplicationQueueService()
        assert isinstance(replication_queue_service, BaseNotificationReplicationQueueService)
        with self.assertRaises(NotImplementedError):
            replication_queue_service.enqueue_replication(
                notification_id=1, backend_identifier="backend-1"
            )


class ImplementationTemplateAsyncIOReplicationQueueServiceTestCase(IsolatedAsyncioTestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(
            ImplementationTemplateAsyncIOReplicationQueueService,
            AsyncIOBaseNotificationReplicationQueueService,
        )

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBaseNotificationReplicationQueueService.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOReplicationQueueService, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert (
            ImplementationTemplateAsyncIOReplicationQueueService.__abstractmethods__
            == frozenset()
        )

    async def test_stub_is_instantiable_and_fails_loudly_on_enqueue(self):
        replication_queue_service = ImplementationTemplateAsyncIOReplicationQueueService()
        assert isinstance(
            replication_queue_service, AsyncIOBaseNotificationReplicationQueueService
        )
        with self.assertRaises(NotImplementedError):
            await replication_queue_service.enqueue_replication(
                notification_id=1, backend_identifier="backend-1"
            )
