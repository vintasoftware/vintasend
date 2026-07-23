import uuid
from unittest import IsolatedAsyncioTestCase, TestCase

import pytest

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import NotificationRenderError
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_backends.stubs.fake_backend import FakeFileBackend
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
)
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    EmailTemplateContent,
    TemplatedEmail,
)


def _build_notification(**overrides) -> Notification:
    """A Notification built in memory -- render_email_template_from_content performs no I/O,
    so nothing here needs to be persisted through a backend."""
    defaults: dict = {
        "id": str(uuid.uuid4()),
        "user_id": 1,
        "notification_type": NotificationTypes.EMAIL.value,
        "title": "Test Notification",
        "body_template": "stored/body.html",
        "context_name": "test_context",
        "context_kwargs": NotificationContextDict({"name": "Ada"}),
        "send_after": None,
        "subject_template": "stored/subject.txt",
        "preheader_template": "stored/preheader.html",
        "status": NotificationStatus.PENDING_SEND.value,
        "context_used": {"name": "Ada"},
    }
    defaults.update(overrides)
    return Notification(**defaults)


class RenderEmailTemplateFromContentTestCase(TestCase):
    def setup_method(self, method):
        self.database_file_name = f"render-from-content-{uuid.uuid4()}.json"

    def teardown_method(self, method):
        FakeFileBackend(database_file_name=self.database_file_name).clear()

    def build_service(self, **kwargs) -> NotificationService:
        kwargs.setdefault(
            "notification_adapters",
            [
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
        )
        kwargs.setdefault(
            "notification_backend",
            "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend",
        )
        kwargs.setdefault(
            "notification_backend_kwargs",
            {"database_file_name": self.database_file_name},
        )
        return NotificationService(**kwargs)

    def test_renders_supplied_content(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="New Subject", body_template="New Body")

        rendered = service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered == TemplatedEmail(subject="New Subject", body="New Body", preheader=None)

    def test_renders_supplied_content_not_stored_templates(self):
        service = self.build_service()
        notification = _build_notification(
            subject_template="Stored Subject",
            body_template="Stored Body",
        )
        content = EmailTemplateContent(
            subject_template="Historical Subject",
            body_template="Historical Body",
        )

        rendered = service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.subject != notification.subject_template
        assert rendered.body != notification.body_template
        assert rendered.subject == "Historical Subject"
        assert rendered.body == "Historical Body"

    def test_preheader_renders_when_provided(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(
            subject_template="Subject",
            body_template="Body",
            preheader_template="Historical Preheader",
        )

        rendered = service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.preheader == "Historical Preheader"

    def test_preheader_is_none_when_not_provided(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="Subject", body_template="Body")

        rendered = service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.preheader is None

    def test_raises_when_notification_type_has_no_email_renderer(self):
        service = self.build_service(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
        )
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="Subject", body_template="Body")

        with pytest.raises(NotificationRenderError):
            service.render_email_template_from_content(
                notification, content, NotificationContextDict({"name": "Ada"})
            )

    def test_reconstructs_a_past_render_from_stored_context_used(self):
        """Audit use-case: an old template pair, replayed against a notification's stored
        context_used, reproduces what that pair would have rendered at send time."""
        service = self.build_service()
        notification = _build_notification(
            subject_template="Current Subject",
            body_template="Current Body",
            context_used={"name": "Ada"},
        )
        old_content = EmailTemplateContent(
            subject_template="Old Subject",
            body_template="Old Body",
            preheader_template="Old Preheader",
        )

        rendered = service.render_email_template_from_content(
            notification,
            old_content,
            NotificationContextDict(notification.context_used),
        )

        assert rendered == TemplatedEmail(
            subject="Old Subject", body="Old Body", preheader="Old Preheader"
        )
        assert rendered.subject != notification.subject_template
        assert rendered.body != notification.body_template


class AsyncIORenderEmailTemplateFromContentTestCase(IsolatedAsyncioTestCase):
    def setup_method(self, method):
        self.database_file_name = f"async-render-from-content-{uuid.uuid4()}.json"

    def teardown_method(self, method):
        FakeFileBackend(database_file_name=self.database_file_name).clear()

    def build_service(self, **kwargs) -> AsyncIONotificationService:
        kwargs.setdefault(
            "notification_adapters",
            [
                (
                    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
        )
        kwargs.setdefault(
            "notification_backend",
            "vintasend.services.notification_backends.stubs.fake_backend.FakeAsyncIOFileBackend",
        )
        kwargs.setdefault(
            "notification_backend_kwargs",
            {"database_file_name": self.database_file_name},
        )
        return AsyncIONotificationService(**kwargs)

    @pytest.mark.asyncio
    async def test_renders_supplied_content(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="New Subject", body_template="New Body")

        rendered = await service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered == TemplatedEmail(subject="New Subject", body="New Body", preheader=None)

    @pytest.mark.asyncio
    async def test_renders_supplied_content_not_stored_templates(self):
        service = self.build_service()
        notification = _build_notification(
            subject_template="Stored Subject",
            body_template="Stored Body",
        )
        content = EmailTemplateContent(
            subject_template="Historical Subject",
            body_template="Historical Body",
        )

        rendered = await service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.subject != notification.subject_template
        assert rendered.body != notification.body_template
        assert rendered.subject == "Historical Subject"
        assert rendered.body == "Historical Body"

    @pytest.mark.asyncio
    async def test_preheader_renders_when_provided(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(
            subject_template="Subject",
            body_template="Body",
            preheader_template="Historical Preheader",
        )

        rendered = await service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.preheader == "Historical Preheader"

    @pytest.mark.asyncio
    async def test_preheader_is_none_when_not_provided(self):
        service = self.build_service()
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="Subject", body_template="Body")

        rendered = await service.render_email_template_from_content(
            notification, content, NotificationContextDict({"name": "Ada"})
        )

        assert rendered.preheader is None

    @pytest.mark.asyncio
    async def test_raises_when_notification_type_has_no_email_renderer(self):
        service = self.build_service(
            notification_adapters=[
                (
                    "vintasend.services.notification_adapters.stubs.fake_in_app_adapter.FakeAsyncIOInAppAdapter",
                    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
                )
            ],
        )
        notification = _build_notification()
        content = EmailTemplateContent(subject_template="Subject", body_template="Body")

        with pytest.raises(NotificationRenderError):
            await service.render_email_template_from_content(
                notification, content, NotificationContextDict({"name": "Ada"})
            )

    @pytest.mark.asyncio
    async def test_reconstructs_a_past_render_from_stored_context_used(self):
        """Audit use-case: an old template pair, replayed against a notification's stored
        context_used, reproduces what that pair would have rendered at send time."""
        service = self.build_service()
        notification = _build_notification(
            subject_template="Current Subject",
            body_template="Current Body",
            context_used={"name": "Ada"},
        )
        old_content = EmailTemplateContent(
            subject_template="Old Subject",
            body_template="Old Body",
            preheader_template="Old Preheader",
        )

        rendered = await service.render_email_template_from_content(
            notification,
            old_content,
            NotificationContextDict(notification.context_used),
        )

        assert rendered == TemplatedEmail(
            subject="Old Subject", body="Old Body", preheader="Old Preheader"
        )
        assert rendered.subject != notification.subject_template
        assert rendered.body != notification.body_template
