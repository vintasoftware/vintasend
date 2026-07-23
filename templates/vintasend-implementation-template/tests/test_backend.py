from unittest import TestCase

from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend

from vintasend_implementation_template.backend import (
    ImplementationTemplateAsyncIOBackend,
    ImplementationTemplateBackend,
)


class ImplementationTemplateBackendTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateBackend, BaseNotificationBackend)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseNotificationBackend.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateBackend, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateBackend.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable(self):
        backend = ImplementationTemplateBackend()
        assert isinstance(backend, BaseNotificationBackend)

    def test_calling_an_unimplemented_method_fails_loudly(self):
        backend = ImplementationTemplateBackend()
        with self.assertRaises(NotImplementedError):
            list(backend.get_all_pending_notifications())


class ImplementationTemplateAsyncIOBackendTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateAsyncIOBackend, AsyncIOBaseNotificationBackend)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBaseNotificationBackend.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOBackend, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAsyncIOBackend.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable(self):
        backend = ImplementationTemplateAsyncIOBackend()
        assert isinstance(backend, AsyncIOBaseNotificationBackend)
