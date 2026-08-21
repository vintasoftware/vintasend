"""Pinning a notification to a template version, and recording the one that rendered it.

Two separate mechanisms, and the tests are grouped that way:

* ``requested_template_version`` is decided when a notification is created or repointed. It
  is the caller's to set; ``pin_template_versions`` only decides what happens when they do
  not.
* ``used_template_version`` is written at send time from what the renderer reported back
  through the adapter, and answers "which version actually went out" for a notification that
  was never pinned -- the only record of it, since the template has moved on by then.

Both are exercised against ``FakeVersionedTemplateRenderer``, whose ``latest_version`` a test
can move between calls. A renderer that reads files reports no version at all, which is the
default and is covered here too.
"""

import tempfile
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

import pytest

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import UsedTemplateVersionReassignmentError
from vintasend.services.dataclasses import Notification, NotificationContextDict
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_backends.stubs.fake_backend import (
    FakeAsyncIOFileBackend,
    FakeFileBackend,
)
from vintasend.services.notification_service import (
    AsyncIONotificationService,
    NotificationService,
    register_context,
)
from vintasend.services.notification_template_renderers.stubs.fake_templated_email_renderer import (
    FakeVersionedTemplateRenderer,
)


VERSIONED_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeVersionedTemplateRenderer"
)
RAISING_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeRaisingVersionTemplateRenderer"
)
PLAIN_RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeTemplateRenderer"
)
SYNC_ADAPTER = "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter"
ASYNC_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncIOEmailAdapter"
)


@register_context("template_version_test_context")
def _template_version_test_context() -> NotificationContextDict:
    return NotificationContextDict({"test": "test"})


def _create_kwargs(**overrides) -> dict:
    defaults: dict = {
        "user_id": 1,
        "notification_type": NotificationTypes.EMAIL.value,
        "title": "Test Notification",
        "body_template": "welcome",
        "context_name": "template_version_test_context",
        "context_kwargs": NotificationContextDict({}),
        "send_after": None,
        "subject_template": "subject",
        "preheader_template": "preheader",
    }
    defaults.update(overrides)
    return defaults


def _one_off_kwargs(**overrides) -> dict:
    defaults = _create_kwargs(**overrides)
    defaults.pop("user_id")
    defaults.update(
        {
            "email_or_phone": "someone@example.com",
            "first_name": "Some",
            "last_name": "One",
        }
    )
    return defaults


class TemplateVersionPinningTestCase(TestCase):
    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeFileBackend(database_file_name=self.database_file_name)
        FakeVersionedTemplateRenderer.latest_version = 7

    def tearDown(self):
        self.backend.clear()
        FakeVersionedTemplateRenderer.latest_version = 7

    def build_service(self, renderer: str = VERSIONED_RENDERER, **kwargs) -> NotificationService:
        kwargs.setdefault("notification_adapters", [(SYNC_ADAPTER, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return NotificationService(**kwargs)

    # -- creating -------------------------------------------------------

    def test_pinning_is_off_unless_asked_for(self):
        service = self.build_service()

        notification = service.create_notification(**_create_kwargs())

        assert notification.requested_template_version is None

    def test_pinning_records_the_version_that_is_current_now(self):
        service = self.build_service(pin_template_versions=True)

        notification = service.create_notification(**_create_kwargs())

        assert notification.requested_template_version == 7

    def test_a_pinned_notification_is_unaffected_by_a_later_template_edit(self):
        service = self.build_service(pin_template_versions=True)
        notification = service.create_notification(**_create_kwargs())

        FakeVersionedTemplateRenderer.latest_version = 8

        assert service.get_notification(notification.id).requested_template_version == 7

    def test_an_explicit_version_wins_over_pinning(self):
        service = self.build_service(pin_template_versions=True)

        notification = service.create_notification(**_create_kwargs(requested_template_version=3))

        assert notification.requested_template_version == 3

    def test_an_explicit_version_is_recorded_with_pinning_off(self):
        service = self.build_service()

        notification = service.create_notification(**_create_kwargs(requested_template_version=3))

        assert notification.requested_template_version == 3

    def test_a_renderer_that_does_not_version_templates_pins_nothing(self):
        service = self.build_service(renderer=PLAIN_RENDERER, pin_template_versions=True)

        notification = service.create_notification(**_create_kwargs())

        assert notification.requested_template_version is None

    def test_a_renderer_that_raises_leaves_the_notification_unpinned(self):
        service = self.build_service(renderer=RAISING_RENDERER, pin_template_versions=True)

        notification = service.create_notification(**_create_kwargs())

        assert notification.requested_template_version is None

    def test_a_renderer_for_another_notification_type_is_not_consulted(self):
        service = self.build_service(pin_template_versions=True)

        notification = service.create_notification(
            **_create_kwargs(notification_type=NotificationTypes.SMS.value, send_after=None)
        )

        assert notification.requested_template_version is None

    def test_a_one_off_notification_pins_the_same_way(self):
        service = self.build_service(pin_template_versions=True)

        notification = service.create_one_off_notification(**_one_off_kwargs())

        assert notification.requested_template_version == 7

    def test_an_unpinned_create_never_passes_the_keyword_to_the_backend(self):
        service = self.build_service()

        with patch.object(
            self.backend, "persist_notification", wraps=self.backend.persist_notification
        ) as persist:
            service.create_notification(**_create_kwargs())

        # A backend written before template versioning does not accept the keyword at all.
        assert "requested_template_version" not in persist.call_args.kwargs

    # -- pinning decided per call ---------------------------------------

    def test_a_single_call_can_pin_in_a_service_that_does_not(self):
        service = self.build_service()

        notification = service.create_notification(**_create_kwargs(pin_template_versions=True))

        assert notification.requested_template_version == 7

    def test_a_single_call_can_decline_to_pin_in_a_service_that_does(self):
        service = self.build_service(pin_template_versions=True)

        notification = service.create_notification(**_create_kwargs(pin_template_versions=False))

        assert notification.requested_template_version is None

    def test_an_explicit_version_wins_over_a_per_call_refusal_to_pin(self):
        service = self.build_service()

        notification = service.create_notification(
            **_create_kwargs(requested_template_version=3, pin_template_versions=False)
        )

        assert notification.requested_template_version == 3

    def test_a_one_off_call_can_pin_on_its_own(self):
        service = self.build_service()

        notification = service.create_one_off_notification(
            **_one_off_kwargs(pin_template_versions=True)
        )

        assert notification.requested_template_version == 7

    def test_an_update_can_pin_in_a_service_that_does_not(self):
        service = self.build_service()
        notification = service.create_notification(**_create_kwargs())

        updated = service.update_notification(
            notification.id, pin_template_versions=True, body_template="farewell"
        )

        assert updated.requested_template_version == 7

    def test_an_update_can_decline_to_pin_in_a_service_that_does(self):
        service = self.build_service(pin_template_versions=True)
        notification = service.create_notification(**_create_kwargs())

        updated = service.update_notification(
            notification.id, pin_template_versions=False, body_template="farewell"
        )

        assert updated.requested_template_version == 7  # the pin it was created with, unmoved

    def test_the_per_call_flag_is_never_written_as_a_field(self):
        service = self.build_service()
        notification = service.create_notification(**_create_kwargs())

        with patch.object(
            self.backend,
            "persist_notification_update",
            wraps=self.backend.persist_notification_update,
        ) as persist:
            service.update_notification(
                notification.id, pin_template_versions=True, title="Renamed"
            )

        # It decides how the update behaves; it is not one of the fields being updated.
        assert "pin_template_versions" not in persist.call_args.kwargs["update_data"]

    # -- updating -------------------------------------------------------

    def test_repointing_at_another_template_re_pins(self):
        service = self.build_service(pin_template_versions=True)
        notification = service.create_notification(**_create_kwargs(send_after=None, title="Draft"))
        FakeVersionedTemplateRenderer.latest_version = 9

        updated = service.update_notification(notification.id, body_template="farewell")

        assert updated.requested_template_version == 9

    def test_an_explicit_version_wins_on_update_too(self):
        service = self.build_service(pin_template_versions=True)
        notification = service.create_notification(**_create_kwargs())

        updated = service.update_notification(
            notification.id, body_template="farewell", requested_template_version=2
        )

        assert updated.requested_template_version == 2

    def test_an_update_that_leaves_the_template_alone_leaves_the_pin_alone(self):
        service = self.build_service(pin_template_versions=True)
        notification = service.create_notification(**_create_kwargs())
        FakeVersionedTemplateRenderer.latest_version = 9

        updated = service.update_notification(notification.id, title="Renamed")

        assert updated.requested_template_version == 7

    def test_repointing_with_pinning_off_pins_nothing(self):
        service = self.build_service()
        notification = service.create_notification(**_create_kwargs())

        updated = service.update_notification(notification.id, body_template="farewell")

        assert updated.requested_template_version is None

    def test_used_template_version_cannot_be_set_by_hand(self):
        service = self.build_service()
        notification = service.create_notification(**_create_kwargs())

        with pytest.raises(UsedTemplateVersionReassignmentError):
            service.update_notification(notification.id, used_template_version=3)

    # -- sending --------------------------------------------------------

    def test_sending_records_the_version_the_renderer_reported(self):
        service = self.build_service()

        notification = service.create_notification(**_create_kwargs())

        assert service.get_notification(notification.id).used_template_version == 7

    def test_a_pinned_notification_records_the_version_it_was_pinned_to(self):
        service = self.build_service()

        notification = service.create_notification(**_create_kwargs(requested_template_version=4))

        assert service.get_notification(notification.id).used_template_version == 4

    def test_a_renderer_that_does_not_version_templates_records_nothing(self):
        service = self.build_service(renderer=PLAIN_RENDERER)

        notification = service.create_notification(**_create_kwargs())

        assert service.get_notification(notification.id).used_template_version is None

    def test_an_unchanged_version_is_not_written_again(self):
        service = self.build_service()
        notification = service.create_notification(**_create_kwargs())

        with patch.object(
            self.backend, "store_template_version", wraps=self.backend.store_template_version
        ) as store:
            service.send(service.get_notification(notification.id))

        store.assert_not_called()

    def test_the_seam_default_stores_nothing_and_raises_nothing(self):
        # What a backend that predates template versioning inherits: it is handed the version
        # and does nothing with it, rather than failing the send for lack of a column.
        assert BaseNotificationBackend.store_template_version(self.backend, "any-id", 3) is None

    def test_a_backend_that_cannot_store_it_still_delivers(self):
        service = self.build_service()

        with patch.object(FakeFileBackend, "store_template_version", lambda *args, **kwargs: None):
            notification = service.create_notification(**_create_kwargs())

        assert service.get_notification(notification.id).status == NotificationStatus.SENT.value

    def test_a_failure_to_record_does_not_fail_the_send(self):
        service = self.build_service(raise_on_failed_send=True)

        with patch.object(
            self.backend, "store_template_version", side_effect=RuntimeError("db down")
        ):
            notification = service.create_notification(**_create_kwargs())

        assert service.get_notification(notification.id).status == NotificationStatus.SENT.value


class AsyncIOTemplateVersionPinningTestCase(IsolatedAsyncioTestCase):
    def setUp(self):
        self.database_file_name = tempfile.mktemp(suffix=".json")
        self.backend = FakeAsyncIOFileBackend(database_file_name=self.database_file_name)
        FakeVersionedTemplateRenderer.latest_version = 7

    def tearDown(self):
        FakeFileBackend(database_file_name=self.database_file_name).clear()
        FakeVersionedTemplateRenderer.latest_version = 7

    def build_service(
        self, renderer: str = VERSIONED_RENDERER, **kwargs
    ) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [(ASYNC_ADAPTER, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return AsyncIONotificationService(**kwargs)

    @pytest.mark.asyncio
    async def test_pinning_is_off_unless_asked_for(self):
        service = self.build_service()

        notification = await service.create_notification(**_create_kwargs())

        assert notification.requested_template_version is None

    @pytest.mark.asyncio
    async def test_pinning_records_the_version_that_is_current_now(self):
        service = self.build_service(pin_template_versions=True)

        notification = await service.create_notification(**_create_kwargs())

        assert notification.requested_template_version == 7

    @pytest.mark.asyncio
    async def test_a_single_call_can_pin_in_a_service_that_does_not(self):
        service = self.build_service()

        notification = await service.create_notification(
            **_create_kwargs(pin_template_versions=True)
        )

        assert notification.requested_template_version == 7

    @pytest.mark.asyncio
    async def test_a_single_call_can_decline_to_pin_in_a_service_that_does(self):
        service = self.build_service(pin_template_versions=True)

        notification = await service.create_notification(
            **_create_kwargs(pin_template_versions=False)
        )

        assert notification.requested_template_version is None

    @pytest.mark.asyncio
    async def test_an_update_can_pin_in_a_service_that_does_not(self):
        service = self.build_service()
        notification = await service.create_notification(**_create_kwargs())

        updated = await service.update_notification(
            notification.id, pin_template_versions=True, body_template="farewell"
        )

        assert updated.requested_template_version == 7

    @pytest.mark.asyncio
    async def test_an_explicit_version_wins_over_pinning(self):
        service = self.build_service(pin_template_versions=True)

        notification = await service.create_notification(
            **_create_kwargs(requested_template_version=3)
        )

        assert notification.requested_template_version == 3

    @pytest.mark.asyncio
    async def test_sending_records_the_version_the_renderer_reported(self):
        service = self.build_service()

        notification = await service.create_notification(**_create_kwargs())

        stored = await service.get_notification(notification.id)
        assert stored.used_template_version == 7

    @pytest.mark.asyncio
    async def test_repointing_at_another_template_re_pins(self):
        service = self.build_service(pin_template_versions=True)
        notification = await service.create_notification(**_create_kwargs())
        FakeVersionedTemplateRenderer.latest_version = 9

        updated = await service.update_notification(notification.id, body_template="farewell")

        assert updated.requested_template_version == 9

    @pytest.mark.asyncio
    async def test_used_template_version_cannot_be_set_by_hand(self):
        service = self.build_service()
        notification = await service.create_notification(**_create_kwargs())

        with pytest.raises(UsedTemplateVersionReassignmentError):
            await service.update_notification(notification.id, used_template_version=3)


class NotificationDataclassDefaultsTestCase(TestCase):
    """Both fields default to None, so a record built by hand needs to say nothing."""

    def test_both_fields_default_to_none(self):
        notification = Notification(
            id="x",
            user_id=1,
            notification_type=NotificationTypes.EMAIL.value,
            title="t",
            body_template="b",
            context_name="template_version_test_context",
            context_kwargs=NotificationContextDict({}),
            send_after=None,
            subject_template="s",
            preheader_template="p",
            status=NotificationStatus.PENDING_SEND.value,
        )

        assert notification.requested_template_version is None
        assert notification.used_template_version is None
