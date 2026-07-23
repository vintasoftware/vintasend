import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import GitCommitShaReassignmentError, InvalidGitCommitShaError
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.git_commit_sha_providers.asyncio_base import AsyncIOBaseGitCommitShaProvider
from vintasend.services.git_commit_sha_providers.base import BaseGitCommitShaProvider
from vintasend.services.git_commit_sha_providers.stubs.fake_git_commit_sha_provider import (
    FAKE_GIT_COMMIT_SHA,
    FakeAsyncIOGitCommitShaProvider,
    FakeGitCommitShaProvider,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_queue_services.stubs.fake_queue_service import (
    FakeAsyncIOQueueService,
    FakeQueueService,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.services.service_utils import normalize_git_commit_sha


OTHER_SHA = "b" * 40


@register_context("git_sha_test_context")
def _git_sha_test_context() -> NotificationContextDict:
    return NotificationContextDict({"test": "test"})


class RaisingGitCommitShaProvider(BaseGitCommitShaProvider):
    """A provider that always raises -- models a broken host implementation."""

    def get_current_git_commit_sha(self) -> str | None:
        raise RuntimeError("boom")


class RaisingAsyncIOGitCommitShaProvider(AsyncIOBaseGitCommitShaProvider):
    """AsyncIO twin of RaisingGitCommitShaProvider."""

    async def get_current_git_commit_sha(self) -> str | None:
        raise RuntimeError("boom")


def _build_notification(notification_id: str, **overrides) -> Notification:
    defaults: dict = {
        "id": notification_id,
        "user_id": 1,
        "notification_type": NotificationTypes.EMAIL.value,
        "title": "Test Notification",
        "body_template": "vintasend_django/emails/test/test_templated_email_body.html",
        "context_name": "git_sha_test_context",
        "context_kwargs": NotificationContextDict({}),
        "send_after": None,
        "subject_template": "vintasend_django/emails/test/test_templated_email_subject.txt",
        "preheader_template": "vintasend_django/emails/test/test_templated_email_preheader.html",
        "status": NotificationStatus.PENDING_SEND.value,
    }
    defaults.update(overrides)
    return Notification(**defaults)


class NormalizeGitCommitShaTestCase(TestCase):
    """Unit tests for the shared normalize_git_commit_sha utility."""

    def test_clean_forty_char_sha_is_unchanged(self):
        sha = "a" * 40
        assert normalize_git_commit_sha(sha) == sha

    def test_uppercase_sha_is_lowercased(self):
        assert normalize_git_commit_sha("A" * 40) == "a" * 40

    def test_padded_sha_is_trimmed(self):
        assert normalize_git_commit_sha("  " + "a" * 40 + "\n") == "a" * 40

    def test_short_sha_raises(self):
        with pytest.raises(InvalidGitCommitShaError):
            normalize_git_commit_sha("a" * 7)

    def test_branch_name_raises(self):
        with pytest.raises(InvalidGitCommitShaError):
            normalize_git_commit_sha("main")

    def test_empty_string_raises(self):
        with pytest.raises(InvalidGitCommitShaError):
            normalize_git_commit_sha("")


class GitCommitShaSyncServiceTestCase(TestCase):
    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)

    def tearDown(self):
        self.backend.clear()

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
        kwargs.setdefault("notification_backend", self.backend)
        return NotificationService(**kwargs)

    def _seed(self, notification: Notification) -> Notification:
        self.backend.notifications.append(notification)
        self.backend._store_notifications()
        return notification

    def test_send_with_no_provider_leaves_git_commit_sha_none_and_never_stores(self):
        notification = self._seed(_build_notification("no-provider"))
        service = self.build_service()

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            service.send(notification)

        spy.assert_not_called()
        assert service.get_notification(notification.id).git_commit_sha is None

    def test_send_with_provider_stores_normalized_sha_once(self):
        notification = self._seed(_build_notification("stores-once"))
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider())

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            service.send(notification)

        spy.assert_called_once_with(notification.id, FAKE_GIT_COMMIT_SHA)
        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert service.get_notification(notification.id).git_commit_sha == FAKE_GIT_COMMIT_SHA

    def test_provider_instance_and_import_string_are_both_accepted(self):
        notification = self._seed(_build_notification("import-string-provider"))
        service = self.build_service(
            git_commit_sha_provider="vintasend.services.git_commit_sha_providers.stubs."
            "fake_git_commit_sha_provider.FakeGitCommitShaProvider",
        )

        service.send(notification)

        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA

    def test_second_send_on_same_object_with_same_sha_does_not_restore(self):
        notification = self._seed(_build_notification("no-restore"))
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider())

        service.send(notification)

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            service.send(notification)

        spy.assert_not_called()

    def test_changed_sha_restores(self):
        notification = self._seed(_build_notification("restores-on-change"))
        provider = FakeGitCommitShaProvider(sha=FAKE_GIT_COMMIT_SHA)
        service = self.build_service(git_commit_sha_provider=provider)

        service.send(notification)

        provider.sha = OTHER_SHA
        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            service.send(notification)

        spy.assert_called_once_with(notification.id, OTHER_SHA)
        assert notification.git_commit_sha == OTHER_SHA
        assert service.get_notification(notification.id).git_commit_sha == OTHER_SHA

    def test_provider_returning_none_skips_the_write(self):
        notification = self._seed(_build_notification("none-skips"))
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider(sha=None))

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            service.send(notification)

        spy.assert_not_called()
        assert notification.git_commit_sha is None

    def test_provider_raising_is_swallowed_to_none_and_send_still_succeeds(self):
        notification = self._seed(_build_notification("provider-raises"))
        service = self.build_service(git_commit_sha_provider=RaisingGitCommitShaProvider())

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                service.send(notification)  # must not raise

        spy.assert_not_called()
        mocked_logger.exception.assert_called_once()
        assert notification.git_commit_sha is None
        stored = service.get_notification(notification.id)
        assert stored.status == NotificationStatus.SENT.value

    def test_send_with_malformed_provider_return_raises(self):
        notification = self._seed(_build_notification("malformed"))
        service = self.build_service(
            git_commit_sha_provider=FakeGitCommitShaProvider(sha="not-a-real-sha")
        )

        with pytest.raises(InvalidGitCommitShaError):
            service.send(notification)

    def test_create_notification_with_provider_round_trips_git_commit_sha(self):
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider())

        notification = service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert service.get_notification(notification.id).git_commit_sha == FAKE_GIT_COMMIT_SHA

    def test_create_notification_with_no_provider_leaves_git_commit_sha_none(self):
        service = self.build_service()

        notification = service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert notification.git_commit_sha is None

    def test_delayed_send_records_the_workers_git_commit_sha(self):
        """The worker resolves its own SHA through its own provider, independent of
        whichever revision enqueued the notification -- the two are separate service
        instances here, exactly as a web process and a worker process would be."""
        queue_service = FakeQueueService()
        background_adapter = (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
            "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
        )

        enqueuing_service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
        )
        with freeze_time("2026-01-01T00:00:00+00:00"):
            notification = enqueuing_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="git_sha_test_context",
                context_kwargs=NotificationContextDict({}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )
        assert notification.git_commit_sha is None

        worker_service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
            git_commit_sha_provider=FakeGitCommitShaProvider(),
        )
        with freeze_time("2026-01-02T00:00:00+00:00"):
            worker_service.delayed_send(queue_service.enqueued_notification_ids[0])

        stored = worker_service.get_notification(notification.id)
        assert stored.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert stored.status == NotificationStatus.SENT.value

    def test_delayed_send_does_not_touch_git_commit_sha_of_an_already_delivered_notification(self):
        """Redelivery (at-least-once) of an already-sent row must not overwrite its SHA
        with an unrelated, later revision -- the notification was not actually re-sent."""
        queue_service = FakeQueueService()
        background_adapter = (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
            "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
        )
        service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
            git_commit_sha_provider=FakeGitCommitShaProvider(),
        )
        notification = service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        service.delayed_send(queue_service.enqueued_notification_ids[0])
        assert service.get_notification(notification.id).git_commit_sha == FAKE_GIT_COMMIT_SHA

        # Simulate redelivery under a different revision after the notification was
        # already marked SENT.
        service.git_commit_sha_provider = FakeGitCommitShaProvider(sha=OTHER_SHA)
        service.delayed_send(notification.id)

        assert service.get_notification(notification.id).git_commit_sha == FAKE_GIT_COMMIT_SHA

    def test_update_notification_with_git_commit_sha_raises(self):
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider())
        notification = service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with pytest.raises(GitCommitShaReassignmentError):
            service.update_notification(
                notification_id=notification.id,
                git_commit_sha=OTHER_SHA,  # type: ignore[call-arg]
            )

        unchanged = service.get_notification(notification.id)
        assert unchanged.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert unchanged.title == "Test Notification"

    def test_update_notification_with_git_commit_sha_via_raw_kwargs_raises(self):
        """UpdateNotificationKwargs deliberately has no git_commit_sha key, so
        update_notification must check the raw kwargs dict, not just the TypedDict."""
        service = self.build_service(git_commit_sha_provider=FakeGitCommitShaProvider())
        notification = service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        untyped_kwargs: dict = {"title": "New title", "git_commit_sha": OTHER_SHA}

        with pytest.raises(GitCommitShaReassignmentError):
            service.update_notification(notification.id, **untyped_kwargs)

        unchanged = service.get_notification(notification.id)
        assert unchanged.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert unchanged.title == "Test Notification"


class GitCommitShaAsyncServiceTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)

    def tearDown(self):
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
        kwargs.setdefault("notification_backend", self.backend)
        return AsyncIONotificationService(**kwargs)

    async def _seed(self, notification: Notification) -> Notification:
        self.backend.notifications.append(notification)
        await self.backend._store_notifications()
        return notification

    @pytest.mark.asyncio
    async def test_send_with_no_provider_leaves_git_commit_sha_none_and_never_stores(self):
        notification = await self._seed(_build_notification("no-provider"))
        service = self.build_service()

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.send(notification)

        spy.assert_not_called()
        assert (await service.get_notification(notification.id)).git_commit_sha is None

    @pytest.mark.asyncio
    async def test_send_with_provider_stores_normalized_sha_once(self):
        notification = await self._seed(_build_notification("stores-once"))
        service = self.build_service(git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider())

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.send(notification)

        spy.assert_called_once_with(notification.id, FAKE_GIT_COMMIT_SHA, None)
        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert (
            await service.get_notification(notification.id)
        ).git_commit_sha == FAKE_GIT_COMMIT_SHA

    @pytest.mark.asyncio
    async def test_provider_instance_and_import_string_are_both_accepted(self):
        notification = await self._seed(_build_notification("import-string-provider"))
        service = self.build_service(
            git_commit_sha_provider="vintasend.services.git_commit_sha_providers.stubs."
            "fake_git_commit_sha_provider.FakeAsyncIOGitCommitShaProvider",
        )

        await service.send(notification)

        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA

    @pytest.mark.asyncio
    async def test_second_send_on_same_object_with_same_sha_does_not_restore(self):
        notification = await self._seed(_build_notification("no-restore"))
        service = self.build_service(git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider())

        await service.send(notification)

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.send(notification)

        spy.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_sha_restores(self):
        notification = await self._seed(_build_notification("restores-on-change"))
        provider = FakeAsyncIOGitCommitShaProvider(sha=FAKE_GIT_COMMIT_SHA)
        service = self.build_service(git_commit_sha_provider=provider)

        await service.send(notification)

        provider.sha = OTHER_SHA
        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.send(notification)

        spy.assert_called_once_with(notification.id, OTHER_SHA, None)
        assert notification.git_commit_sha == OTHER_SHA

    @pytest.mark.asyncio
    async def test_provider_returning_none_skips_the_write(self):
        notification = await self._seed(_build_notification("none-skips"))
        service = self.build_service(
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(sha=None)
        )

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.send(notification)

        spy.assert_not_called()
        assert notification.git_commit_sha is None

    @pytest.mark.asyncio
    async def test_provider_raising_is_swallowed_to_none_and_send_still_succeeds(self):
        notification = await self._seed(_build_notification("provider-raises"))
        service = self.build_service(git_commit_sha_provider=RaisingAsyncIOGitCommitShaProvider())

        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            with patch("vintasend.services.notification_service.logger") as mocked_logger:
                await service.send(notification)  # must not raise

        spy.assert_not_called()
        mocked_logger.exception.assert_called_once()
        assert notification.git_commit_sha is None
        stored = await service.get_notification(notification.id)
        assert stored.status == NotificationStatus.SENT.value

    @pytest.mark.asyncio
    async def test_send_with_malformed_provider_return_raises(self):
        notification = await self._seed(_build_notification("malformed"))
        service = self.build_service(
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(sha="not-a-real-sha")
        )

        with pytest.raises(InvalidGitCommitShaError):
            await service.send(notification)

    @pytest.mark.asyncio
    async def test_create_notification_with_provider_round_trips_git_commit_sha(self):
        service = self.build_service(git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider())

        notification = await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert notification.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert (
            await service.get_notification(notification.id)
        ).git_commit_sha == FAKE_GIT_COMMIT_SHA

    @pytest.mark.asyncio
    async def test_create_notification_with_no_provider_leaves_git_commit_sha_none(self):
        service = self.build_service()

        notification = await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        assert notification.git_commit_sha is None

    @pytest.mark.asyncio
    async def test_delayed_send_records_the_workers_git_commit_sha(self):
        queue_service = FakeAsyncIOQueueService()
        background_adapter = (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOBackgroundEmailAdapter",
            "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
        )

        enqueuing_service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
        )
        with freeze_time("2026-01-01T00:00:00+00:00"):
            notification = await enqueuing_service.create_notification(
                user_id=1,
                notification_type=NotificationTypes.EMAIL.value,
                title="Test Notification",
                body_template="vintasend_django/emails/test/test_templated_email_body.html",
                context_name="git_sha_test_context",
                context_kwargs=NotificationContextDict({}),
                send_after=None,
                subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
                preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
            )
        assert notification.git_commit_sha is None

        worker_service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(),
        )
        with freeze_time("2026-01-02T00:00:00+00:00"):
            await worker_service.delayed_send(queue_service.enqueued_notification_ids[0])

        stored = await worker_service.get_notification(notification.id)
        assert stored.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert stored.status == NotificationStatus.SENT.value

    @pytest.mark.asyncio
    async def test_delayed_send_does_not_touch_git_commit_sha_of_an_already_delivered_notification(
        self,
    ):
        """Redelivery (at-least-once) of an already-sent row must not overwrite its SHA
        with an unrelated, later revision -- the notification was not actually re-sent."""
        queue_service = FakeAsyncIOQueueService()
        background_adapter = (
            "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOBackgroundEmailAdapter",
            "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer.FakeTemplateRenderer",
        )
        service = self.build_service(
            notification_adapters=[background_adapter],
            notification_queue_service=queue_service,
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(),
        )
        notification = await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        await service.delayed_send(queue_service.enqueued_notification_ids[0])
        assert (await service.get_notification(notification.id)).git_commit_sha == (
            FAKE_GIT_COMMIT_SHA
        )

        # Simulate redelivery under a different revision after the notification was
        # already marked SENT.
        service.git_commit_sha_provider = FakeAsyncIOGitCommitShaProvider(sha=OTHER_SHA)
        with patch.object(
            self.backend, "store_git_commit_sha", wraps=self.backend.store_git_commit_sha
        ) as spy:
            await service.delayed_send(notification.id)

        spy.assert_not_called()
        assert (
            await service.get_notification(notification.id)
        ).git_commit_sha == FAKE_GIT_COMMIT_SHA

    @pytest.mark.asyncio
    async def test_update_notification_with_git_commit_sha_raises(self):
        service = self.build_service(git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider())
        notification = await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )

        with pytest.raises(GitCommitShaReassignmentError):
            await service.update_notification(
                notification_id=notification.id,
                git_commit_sha=OTHER_SHA,  # type: ignore[call-arg]
            )

        unchanged = await service.get_notification(notification.id)
        assert unchanged.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert unchanged.title == "Test Notification"

    @pytest.mark.asyncio
    async def test_update_notification_with_git_commit_sha_via_raw_kwargs_raises(self):
        service = self.build_service(git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider())
        notification = await service.create_notification(
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="Test Notification",
            body_template="vintasend_django/emails/test/test_templated_email_body.html",
            context_name="git_sha_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="vintasend_django/emails/test/test_templated_email_subject.txt",
            preheader_template="vintasend_django/emails/test/test_templated_email_preheader.html",
        )
        untyped_kwargs: dict = {"title": "New title", "git_commit_sha": OTHER_SHA}

        with pytest.raises(GitCommitShaReassignmentError):
            await service.update_notification(notification.id, **untyped_kwargs)

        unchanged = await service.get_notification(notification.id)
        assert unchanged.git_commit_sha == FAKE_GIT_COMMIT_SHA
        assert unchanged.title == "Test Notification"
