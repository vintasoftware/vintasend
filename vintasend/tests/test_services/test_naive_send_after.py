"""A naive ``send_after`` is read as UTC.

SQLite, and PostgreSQL ``timestamp`` columns, hand datetimes back without a time zone. The
service compares a notification's send time with an aware "now" when it creates, updates,
requeues or resends one, which raised ``TypeError`` for a naive value before 3.8.0.
"""

import datetime
import tempfile
import uuid
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.services.dataclasses import NotificationContextDict
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)


PLAIN_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer."
    "FakeTemplateRenderer"
)
SYNC_ADAPTER = "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter"
ASYNCIO_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter"
)

PENDING = NotificationStatus.PENDING_SEND.value
SENT = NotificationStatus.SENT.value


@register_context("naive_send_after_test_context")
def _naive_send_after_test_context() -> NotificationContextDict:
    return NotificationContextDict({})


def _naive(delta: datetime.timedelta) -> datetime.datetime:
    """A UTC time without a time zone, as SQLite returns it."""
    return (datetime.datetime.now(tz=datetime.timezone.utc) + delta).replace(tzinfo=None)


TOMORROW = datetime.timedelta(days=1)
YESTERDAY = -TOMORROW


def _create_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "user_id": 1,
        "notification_type": NotificationTypes.EMAIL.value,
        "title": "Title",
        "body_template": "template",
        "context_name": "naive_send_after_test_context",
        "context_kwargs": NotificationContextDict({}),
        "subject_template": "subject",
        "preheader_template": "preheader",
    }
    defaults.update(overrides)
    return defaults


def _database_file() -> str:
    return f"{tempfile.gettempdir()}/naive-send-after-{uuid.uuid4().hex}.json"


class NaiveSendAfterTestCase(TestCase):
    def setUp(self):
        self.backend = FakeFileBackend(database_file_name=_database_file())
        self.service = NotificationService(
            notification_adapters=[(SYNC_ADAPTER, PLAIN_RENDERER)],
            notification_backend=self.backend,
        )

    def tearDown(self):
        self.backend.clear()

    def test_a_naive_future_send_time_is_not_due(self):
        created = self.service.create_notification(**_create_kwargs(send_after=_naive(TOMORROW)))

        assert self.backend.get_notification(created.id).status == PENDING

    def test_a_naive_past_send_time_is_due(self):
        created = self.service.create_notification(**_create_kwargs(send_after=_naive(YESTERDAY)))

        assert self.backend.get_notification(created.id).status == SENT

    def test_updating_a_notification_with_a_naive_send_time(self):
        created = self.service.create_notification(**_create_kwargs(send_after=_naive(TOMORROW)))

        self.service.update_notification(created.id, title="Changed")

        stored = self.backend.get_notification(created.id)
        assert (stored.status, stored.title) == (PENDING, "Changed")

    def test_a_one_off_notification_with_a_naive_send_time(self):
        kwargs = _create_kwargs(send_after=_naive(TOMORROW))
        kwargs.pop("user_id")
        kwargs.update(
            {"email_or_phone": "someone@example.com", "first_name": "A", "last_name": "B"}
        )

        created = self.service.create_one_off_notification(**kwargs)

        assert self.backend.get_notification(created.id).status == PENDING

    def test_resending_a_notification_with_a_naive_send_time(self):
        created = self.service.create_notification(**_create_kwargs(send_after=_naive(YESTERDAY)))

        clone = self.service.resend_notification(created.id)

        assert self.backend.get_notification(clone.id).status == SENT


class AsyncIONaiveSendAfterTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.backend = FakeAsyncIOFileBackend(database_file_name=_database_file())
        self.service = AsyncIONotificationService(
            notification_adapters=[(ASYNCIO_ADAPTER, PLAIN_RENDERER)],
            notification_backend=self.backend,
        )

    async def asyncTearDown(self):
        await self.backend.clear()

    async def test_a_naive_future_send_time_is_not_due(self):
        created = await self.service.create_notification(
            **_create_kwargs(send_after=_naive(TOMORROW))
        )

        assert (await self.backend.get_notification(created.id)).status == PENDING

    async def test_a_naive_past_send_time_is_due(self):
        created = await self.service.create_notification(
            **_create_kwargs(send_after=_naive(YESTERDAY))
        )

        assert (await self.backend.get_notification(created.id)).status == SENT

    async def test_updating_a_notification_with_a_naive_send_time(self):
        created = await self.service.create_notification(
            **_create_kwargs(send_after=_naive(TOMORROW))
        )

        await self.service.update_notification(created.id, title="Changed")

        stored = await self.backend.get_notification(created.id)
        assert (stored.status, stored.title) == (PENDING, "Changed")

    async def test_a_one_off_notification_with_a_naive_send_time(self):
        kwargs = _create_kwargs(send_after=_naive(TOMORROW))
        kwargs.pop("user_id")
        kwargs.update(
            {"email_or_phone": "someone@example.com", "first_name": "A", "last_name": "B"}
        )

        created = await self.service.create_one_off_notification(**kwargs)

        assert (await self.backend.get_notification(created.id)).status == PENDING

    async def test_resending_a_notification_with_a_naive_send_time(self):
        created = await self.service.create_notification(
            **_create_kwargs(send_after=_naive(YESTERDAY))
        )

        clone = await self.service.resend_notification(created.id)

        assert (await self.backend.get_notification(clone.id)).status == SENT
