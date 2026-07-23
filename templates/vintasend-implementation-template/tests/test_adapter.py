from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
from vintasend.services.notification_adapters.asyncio_background_base import (
    AsyncIOBackgroundNotificationAdapter,
)
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter

from vintasend_implementation_template.adapter import (
    ImplementationTemplateAdapter,
    ImplementationTemplateAsyncIOAdapter,
    ImplementationTemplateAsyncIOBackgroundAdapter,
    ImplementationTemplateBackgroundAdapter,
)
from vintasend_implementation_template.backend import (
    ImplementationTemplateAsyncIOBackend,
    ImplementationTemplateBackend,
)
from vintasend_implementation_template.template_renderer import (
    ImplementationTemplateTemplateRenderer,
)


class ImplementationTemplateAdapterTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateAdapter, BaseNotificationAdapter)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseNotificationAdapter.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAdapter, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAdapter.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_send(self):
        adapter = ImplementationTemplateAdapter(
            ImplementationTemplateTemplateRenderer(), ImplementationTemplateBackend()
        )
        assert isinstance(adapter, BaseNotificationAdapter)
        with self.assertRaises(NotImplementedError):
            adapter.send(notification=None, context=None)  # type: ignore[arg-type]


class ImplementationTemplateBackgroundAdapterTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateBackgroundAdapter, BackgroundNotificationAdapter)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BackgroundNotificationAdapter.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateBackgroundAdapter, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateBackgroundAdapter.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_delayed_send(self):
        adapter = ImplementationTemplateBackgroundAdapter(
            ImplementationTemplateTemplateRenderer(), ImplementationTemplateBackend()
        )
        assert isinstance(adapter, BackgroundNotificationAdapter)
        with self.assertRaises(NotImplementedError):
            adapter.delayed_send(notification_id=1)


class ImplementationTemplateAsyncIOAdapterTestCase(IsolatedAsyncioTestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateAsyncIOAdapter, AsyncIOBaseNotificationAdapter)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBaseNotificationAdapter.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOAdapter, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAsyncIOAdapter.__abstractmethods__ == frozenset()

    async def test_stub_is_instantiable_and_fails_loudly_on_send(self):
        adapter = ImplementationTemplateAsyncIOAdapter(
            ImplementationTemplateTemplateRenderer(), ImplementationTemplateAsyncIOBackend()
        )
        assert isinstance(adapter, AsyncIOBaseNotificationAdapter)
        with self.assertRaises(NotImplementedError):
            await adapter.send(notification=None, context=None)  # type: ignore[arg-type]


class ImplementationTemplateAsyncIOBackgroundAdapterTestCase(IsolatedAsyncioTestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(
            ImplementationTemplateAsyncIOBackgroundAdapter, AsyncIOBackgroundNotificationAdapter
        )

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBackgroundNotificationAdapter.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOBackgroundAdapter, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAsyncIOBackgroundAdapter.__abstractmethods__ == frozenset()

    async def test_stub_is_instantiable_and_fails_loudly_on_delayed_send(self):
        adapter = ImplementationTemplateAsyncIOBackgroundAdapter(
            ImplementationTemplateTemplateRenderer(), ImplementationTemplateAsyncIOBackend()
        )
        assert isinstance(adapter, AsyncIOBackgroundNotificationAdapter)
        with self.assertRaises(NotImplementedError):
            await adapter.delayed_send(notification_id=1)
