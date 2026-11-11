"""The service calls ``persist_notification_update`` positionally.

The seam names its second parameter ``update_data``, but the released vintasend-django and
vintasend-sqlalchemy backends name it ``updated_data``. A keyword call would raise ``TypeError``
on those backends, so these tests run every service path that writes through the method against
a backend that uses the other name.
"""

import datetime
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.services.attachment_managers.stubs.fake_attachment_manager import (
    FakeAsyncIOAttachmentManager,
    FakeAttachmentManager,
)
from vintasend.services.dataclasses import (
    ApplyResult,
    NotificationAttachment,
    NotificationContextDict,
)
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)


RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeTemplateRenderer"
)
SYNC_ADAPTER = "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter"
ASYNCIO_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter"
)


@register_context("persist_update_parameter_name_context")
def _context() -> NotificationContextDict:
    return NotificationContextDict({})


class _UpdatedDataBackend(FakeFileBackend):
    """Names the parameter ``updated_data``, as vintasend-django and vintasend-sqlalchemy do."""

    def persist_notification_update(self, notification_id, updated_data):  # type: ignore[override]
        return super().persist_notification_update(notification_id, updated_data)


class _UpdatedDataNoSnapshotReplica(_UpdatedDataBackend):
    """Declines snapshots, so the service converges it through ``persist_notification_update``."""

    def apply_replication_snapshot_if_newer(self, snapshot):
        return ApplyResult(applied=False, reason="declined for test")


class _AsyncIOUpdatedDataBackend(FakeAsyncIOFileBackend):
    async def persist_notification_update(  # type: ignore[override]
        self, notification_id, updated_data, lock=None
    ):
        return await super().persist_notification_update(notification_id, updated_data, lock)


class _AsyncIOUpdatedDataNoSnapshotReplica(_AsyncIOUpdatedDataBackend):
    async def apply_replication_snapshot_if_newer(self, snapshot):
        return ApplyResult(applied=False, reason="declined for test")


def _new_backend(cls: type) -> Any:
    return cls(database_file_name=tempfile.mktemp(suffix=".json"))


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "user_id": 1,
        "notification_type": "EMAIL",
        "title": "Original title",
        "body_template": "body",
        "context_name": "persist_update_parameter_name_context",
        "context_kwargs": {},
        "send_after": None,
        "subject_template": "subject",
        "preheader_template": "preheader",
    }
    kwargs.update(overrides)
    return kwargs


def _far_future() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(days=30)


def _attachment() -> NotificationAttachment:
    return NotificationAttachment(filename="report.txt", content_type="text/plain", file=b"x")


class PersistUpdateParameterNameTestCase(TestCase):
    def setUp(self):
        self.backend = _new_backend(_UpdatedDataBackend)
        self._backends = [self.backend]

    def tearDown(self):
        for backend in self._backends:
            backend.clear()

    def build_service(self, **kwargs: Any) -> NotificationService:
        return NotificationService(
            notification_adapters=[(SYNC_ADAPTER, RENDERER)],
            notification_backend=self.backend,
            **kwargs,
        )

    def test_update_notification(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs(send_after=_far_future()))

        updated = service.update_notification(created.id, title="New title")

        assert updated.title == "New title"
        assert self.backend.get_notification(created.id).title == "New title"

    def test_resend_notification_copies_attachments_to_the_clone(self):
        service = self.build_service(attachment_manager=FakeAttachmentManager())
        source = service.create_notification(**_create_kwargs(attachments=[_attachment()]))

        clone = service.resend_notification(source.id)

        assert clone.id != source.id
        assert [a.filename for a in clone.attachments] == ["report.txt"]

    def test_repin_notification(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs(send_after=_far_future()))

        repinned = service.repin_notification(created.id, template_version=4)

        assert repinned.requested_template_version == 4

    def test_update_converges_a_replica_that_declines_snapshots(self):
        replica = _new_backend(_UpdatedDataNoSnapshotReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs(send_after=_far_future()))
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)

        service.update_notification(created.id, title="New title")

        assert replica.get_notification(created.id).title == "New title"


class AsyncIOPersistUpdateParameterNameTestCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = _new_backend(_AsyncIOUpdatedDataBackend)
        self._backends = [self.backend]

    async def asyncTearDown(self):
        for backend in self._backends:
            await backend.clear()

    def build_service(self, **kwargs: Any) -> AsyncIONotificationService:
        return AsyncIONotificationService(
            notification_adapters=[(ASYNCIO_ADAPTER, RENDERER)],
            notification_backend=self.backend,
            **kwargs,
        )

    async def test_update_notification(self):
        service = self.build_service()
        created = await service.create_notification(**_create_kwargs(send_after=_far_future()))

        updated = await service.update_notification(created.id, title="New title")

        assert updated.title == "New title"
        assert (await self.backend.get_notification(created.id)).title == "New title"

    async def test_resend_notification_copies_attachments_to_the_clone(self):
        service = self.build_service(attachment_manager=FakeAsyncIOAttachmentManager())
        source = await service.create_notification(**_create_kwargs(attachments=[_attachment()]))

        clone = await service.resend_notification(source.id)

        assert clone.id != source.id
        assert [a.filename for a in clone.attachments] == ["report.txt"]

    async def test_repin_notification(self):
        service = self.build_service()
        created = await service.create_notification(**_create_kwargs(send_after=_far_future()))

        repinned = await service.repin_notification(created.id, template_version=4)

        assert repinned.requested_template_version == 4

    async def test_update_converges_a_replica_that_declines_snapshots(self):
        replica = _new_backend(_AsyncIOUpdatedDataNoSnapshotReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = await service.create_notification(**_create_kwargs(send_after=_far_future()))
        # A replica that declines snapshots cannot receive a created row, so it is seeded.
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica, created)

        await service.update_notification(created.id, title="New title")

        assert (await replica.get_notification(created.id)).title == "New title"
