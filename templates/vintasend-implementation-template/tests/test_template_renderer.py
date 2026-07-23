from unittest import TestCase

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
)
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    BaseTemplatedEmailRenderer,
)
from vintasend.services.notification_template_renderers.base_templated_sms_renderer import (
    BaseTemplatedSMSRenderer,
)

from vintasend_implementation_template.template_renderer import (
    ImplementationTemplateEmailRenderer,
    ImplementationTemplateSMSRenderer,
    ImplementationTemplateTemplateRenderer,
)


class ImplementationTemplateTemplateRendererTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateTemplateRenderer, BaseNotificationTemplateRenderer)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseNotificationTemplateRenderer.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateTemplateRenderer, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateTemplateRenderer.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_render(self):
        renderer = ImplementationTemplateTemplateRenderer()
        assert isinstance(renderer, BaseNotificationTemplateRenderer)
        with self.assertRaises(NotImplementedError):
            renderer.render(notification=None, context=None)  # type: ignore[arg-type]


class ImplementationTemplateEmailRendererTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateEmailRenderer, BaseTemplatedEmailRenderer)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseTemplatedEmailRenderer.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateEmailRenderer, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateEmailRenderer.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_render(self):
        renderer = ImplementationTemplateEmailRenderer()
        assert isinstance(renderer, BaseTemplatedEmailRenderer)
        with self.assertRaises(NotImplementedError):
            renderer.render(notification=None, context=None)  # type: ignore[arg-type]


class ImplementationTemplateSMSRendererTestCase(TestCase):
    def test_subclasses_the_real_abc(self):
        assert issubclass(ImplementationTemplateSMSRenderer, BaseTemplatedSMSRenderer)

    def test_every_abstract_method_of_the_current_abc_is_overridden(self):
        expected = BaseTemplatedSMSRenderer.__abstractmethods__
        assert expected, "the ABC's abstract method set should never be empty"
        for method_name in expected:
            assert hasattr(ImplementationTemplateSMSRenderer, method_name)

    def test_no_abstract_methods_remain_unimplemented(self):
        assert ImplementationTemplateSMSRenderer.__abstractmethods__ == frozenset()

    def test_stub_is_instantiable_and_fails_loudly_on_render(self):
        renderer = ImplementationTemplateSMSRenderer()
        assert isinstance(renderer, BaseTemplatedSMSRenderer)
        with self.assertRaises(NotImplementedError):
            renderer.render(notification=None, context=None)  # type: ignore[arg-type]
