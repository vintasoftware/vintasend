from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
from vintasend.services.attachment_managers.base import BaseAttachmentManager

from vintasend_implementation_template.attachment_manager import (
    ImplementationTemplateAsyncIOAttachmentManager,
    ImplementationTemplateAttachmentManager,
)


class ImplementationTemplateAttachmentManagerTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateAttachmentManager, BaseAttachmentManager)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseAttachmentManager.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAttachmentManager, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAttachmentManager.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_upload(self):
        manager = ImplementationTemplateAttachmentManager()
        assert isinstance(manager, BaseAttachmentManager)
        with self.assertRaises(NotImplementedError):
            manager.upload_file(file=b"data", filename="test.txt")


class ImplementationTemplateAsyncIOAttachmentManagerTestCase(IsolatedAsyncioTestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(
            ImplementationTemplateAsyncIOAttachmentManager, AsyncIOBaseAttachmentManager
        )

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = AsyncIOBaseAttachmentManager.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateAsyncIOAttachmentManager, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateAsyncIOAttachmentManager.__abstractmethods__ == frozenset()

    async def test_stub_is_instantiable_and_fails_loudly_on_upload(self):
        manager = ImplementationTemplateAsyncIOAttachmentManager()
        assert isinstance(manager, AsyncIOBaseAttachmentManager)
        with self.assertRaises(NotImplementedError):
            await manager.upload_file(file=b"data", filename="test.txt")
