"""Conditional repin and requeue, requeue with a pin, the send outcome, and version listing.

Each refusal is checked to write nothing: the notification is read back from the backend
and compared, version token included, with what it was before the call.
"""

import dataclasses
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from freezegun import freeze_time

from vintasend.constants import (
    DEFAULT_BACKEND_ACTION_CAPABILITIES,
    UNSET,
    NotificationStatus,
)
from vintasend.exceptions import (
    InvalidExpectedVersionError,
    InvalidTemplateVersionError,
    NotificationActionError,
    NotificationRepinError,
    NotificationVersionChangedError,
    UnsupportedNotificationActionError,
)
from vintasend.services.dataclasses import ApplyResult, NotificationVersion
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_backends.filters import NotificationFilter, matches_filter
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
)
from vintasend.tests.test_services.test_requeue_and_repin import (
    ASYNCIO_ADAPTER,
    ASYNCIO_BACKGROUND_ADAPTER,
    PLAIN_RENDERER,
    RAISING_RENDERER,
    SYNC_ADAPTER,
    SYNC_BACKGROUND_ADAPTER,
    VERSIONED_RENDERER,
    _amove_to,
    _assert_no_field_values,
    _create_kwargs,
    _future,
    _LogCapture,
    _move_to,
    _new_backend,
    _one_off_kwargs,
    _past,
    _sent,
)


PENDING = NotificationStatus.PENDING_SEND.value
SENT = NotificationStatus.SENT.value
FAILED = NotificationStatus.FAILED.value
CANCELLED = NotificationStatus.CANCELLED.value

FROZEN_AT = "2026-01-15 12:00:00"


# --- backends with fewer capabilities ----------------------------------------------------


class _NoActionsBackend(FakeFileBackend):
    """A backend written before 3.8.0: no options on requeue, no repin, no version listing.

    Its ``requeue_notification`` does not even accept the new keyword arguments, like a
    downstream backend that has not been updated, so any that reached it would raise.
    """

    # The ignore is against FakeFileBackend, whose signature is widened with the 3.8.0
    # keywords; against the 3.7.0-shaped abstract base this override type-checks.
    def requeue_notification(  # type: ignore[override]
        self, notification_id: Any, send_after: Any = UNSET
    ) -> Any:
        return super().requeue_notification(notification_id, send_after)

    repin_notification = BaseNotificationBackend.repin_notification  # type: ignore[assignment]
    filter_notification_versions = BaseNotificationBackend.filter_notification_versions  # type: ignore[assignment]

    def get_action_capabilities(self) -> dict[str, bool]:
        return {}


class _OverclaimingBackend(_NoActionsBackend):
    """Reports every action without implementing repin or version listing."""

    def get_action_capabilities(self) -> dict[str, bool]:
        return {key: True for key in DEFAULT_BACKEND_ACTION_CAPABILITIES}


class _AsyncIONoActionsBackend(FakeAsyncIOFileBackend):
    """The AsyncIO twin of ``_NoActionsBackend``."""

    # Ignored against FakeAsyncIOFileBackend's widened signature, as on the sync twin.
    async def requeue_notification(  # type: ignore[override]
        self, notification_id: Any, send_after: Any = UNSET, lock: Any = None
    ) -> Any:
        return await super().requeue_notification(notification_id, send_after, lock)

    repin_notification = AsyncIOBaseNotificationBackend.repin_notification  # type: ignore[assignment]
    filter_notification_versions = AsyncIOBaseNotificationBackend.filter_notification_versions  # type: ignore[assignment]

    async def get_action_capabilities(self) -> dict[str, bool]:
        return {}


class _AsyncIOOverclaimingBackend(_AsyncIONoActionsBackend):
    """Reports every action without implementing repin or version listing."""

    async def get_action_capabilities(self) -> dict[str, bool]:
        return {key: True for key in DEFAULT_BACKEND_ACTION_CAPABILITIES}


class _DecliningReplica(_NoActionsBackend):
    """A replica that declines snapshots and pins only through ``persist_notification_update``."""

    def apply_replication_snapshot_if_newer(self, snapshot: Any) -> ApplyResult:
        return ApplyResult(applied=False, reason="declined for test")


class _PinningReplica(FakeFileBackend):
    """A replica that declines snapshots but can requeue with a pin in one write."""

    def apply_replication_snapshot_if_newer(self, snapshot: Any) -> ApplyResult:
        return ApplyResult(applied=False, reason="declined for test")


class _AsyncIODecliningReplica(_AsyncIONoActionsBackend):
    """The AsyncIO twin of ``_DecliningReplica``."""

    async def apply_replication_snapshot_if_newer(self, snapshot: Any) -> ApplyResult:
        return ApplyResult(applied=False, reason="declined for test")


class _AsyncIOPinningReplica(FakeAsyncIOFileBackend):
    """The AsyncIO twin of ``_PinningReplica``."""

    async def apply_replication_snapshot_if_newer(self, snapshot: Any) -> ApplyResult:
        return ApplyResult(applied=False, reason="declined for test")


def _stored(backend: FakeFileBackend, notification_id: Any) -> tuple[Any, str]:
    """A copy of the stored notification and its version token, to compare after a refusal."""
    notification = backend.get_notification(notification_id)
    return dataclasses.replace(notification), backend._version_token(notification_id)


async def _astored(backend: FakeAsyncIOFileBackend, notification_id: Any) -> tuple[Any, str]:
    notification = await backend.get_notification(notification_id)
    return dataclasses.replace(notification), backend._version_token(notification_id)


# The filter the spec requires every backend to run: failed notifications of a template, and
# scheduled ones pinned to anything but version 4.
STRANDED_FILTER: NotificationFilter = {
    "and": [
        {"body_template": "welcome"},
        {
            "or": [
                {
                    "and": [
                        {"status": NotificationStatus.PENDING_SEND},
                        {"has_send_after": True},
                        {"has_requested_template_version": True},
                        {"not": {"requested_template_version": 4}},
                    ]
                },
                {"status": NotificationStatus.FAILED},
            ]
        },
    ]
}


# --- sync ---------------------------------------------------------------------------------


class _SyncCase(TestCase):
    def setUp(self):
        self.backend = _new_backend(FakeFileBackend)
        self._backends: list[Any] = [self.backend]

    def tearDown(self):
        for backend in self._backends:
            backend.clear()

    def use_backend(self, cls: type) -> Any:
        self.backend = _new_backend(cls)
        self._backends.append(self.backend)
        return self.backend

    def build_service(
        self, adapter: str = SYNC_ADAPTER, renderer: str = PLAIN_RENDERER, **kwargs: Any
    ) -> NotificationService:
        kwargs.setdefault("notification_adapters", [(adapter, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return NotificationService(**kwargs)

    def make(self, service: NotificationService, status: str, **overrides: Any) -> Any:
        notification = service.create_notification(**_create_kwargs(**overrides))
        _move_to(self.backend, notification.id, status)
        return self.backend.get_notification(notification.id)

    def token(self, notification_id: Any) -> str:
        return self.service_versions()[str(notification_id)]

    def service_versions(self) -> dict[str, str]:
        return {
            str(entry.id): entry.version
            for entry in self.backend.filter_notification_versions({}, 1, 1000)
        }

    def assert_unchanged(self, notification_id: Any, before: tuple[Any, str]) -> None:
        assert _stored(self.backend, notification_id) == before


class ActionCapabilitiesTestCase(_SyncCase):
    def test_the_fake_reports_every_action(self):
        service = self.build_service()
        assert service.get_backend_supported_action_capabilities() == {
            key: True for key in DEFAULT_BACKEND_ACTION_CAPABILITIES
        }

    def test_every_key_defaults_to_false(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        assert service.get_backend_supported_action_capabilities() == {
            key: False for key in DEFAULT_BACKEND_ACTION_CAPABILITIES
        }

    def test_a_key_whose_method_is_inherited_reads_false(self):
        self.use_backend(_OverclaimingBackend)
        capabilities = self.build_service().get_backend_supported_action_capabilities()
        assert capabilities["repin.expectedVersion"] is False
        assert capabilities["versions.filter"] is False
        assert capabilities["requeue.expectedVersion"] is True

    def test_reads_a_named_backend(self):
        replica = _new_backend(_NoActionsBackend)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        assert service.get_backend_supported_action_capabilities("backend-1") == {
            key: False for key in DEFAULT_BACKEND_ACTION_CAPABILITIES
        }


class ConditionalRequeueTestCase(_SyncCase):
    def test_requeues_when_the_version_matches(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(
            notification.id, expected_version=self.token(notification.id), send=False
        )

        assert requeued.status == PENDING

    def test_a_changed_notification_is_refused_and_nothing_is_written(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        listed = self.token(notification.id)
        # Someone else writes to it after it was listed.
        self.backend.store_context_used(notification.id, {"source": "other"}, "adapter")
        before = _stored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(notification.id, expected_version=listed)

        assert raised.exception.reason == "CHANGED"
        assert raised.exception.action == "requeue"
        self.assert_unchanged(notification.id, before)
        assert _sent(service) == []

    def test_an_unknown_token_is_refused_as_changed(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        before = _stored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(notification.id, expected_version="not-a-token")

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(notification.id, before)

    def test_a_status_refusal_wins_over_a_matching_version(self):
        service = self.build_service()
        notification = self.make(service, SENT)
        before = _stored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(
                notification.id, expected_version=self.token(notification.id)
            )

        assert raised.exception.reason == "INVALID_STATUS"
        self.assert_unchanged(notification.id, before)

    def test_a_change_between_the_read_and_the_write_is_refused(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        before = _stored(self.backend, notification.id)

        with patch.object(
            self.backend,
            "requeue_notification",
            side_effect=NotificationVersionChangedError("changed"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                service.requeue_notification(
                    notification.id, expected_version=self.token(notification.id)
                )

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(notification.id, before)

    def test_unsupported_options_are_refused_before_anything_is_read(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        notification = self.make(service, FAILED)
        before = _stored(self.backend, notification.id)

        for options in ({"expected_version": "v"}, {"template_version": 3}):
            with self.subTest(options=options):
                with patch.object(
                    self.backend, "get_notification", wraps=self.backend.get_notification
                ) as reads:
                    with self.assertRaises(NotificationActionError) as raised:
                        service.requeue_notification(notification.id, **options)
                assert raised.exception.reason == "UNSUPPORTED"
                reads.assert_not_called()
                self.assert_unchanged(notification.id, before)

        # The same backend still requeues without the options.
        assert service.requeue_notification(notification.id, send=False).status == PENDING

    def test_an_unknown_id_with_an_unsupported_option_is_unsupported(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification("missing", expected_version="v")

        assert raised.exception.reason == "UNSUPPORTED"

    def test_an_invalid_expected_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        for value in ("", 3, True):
            with self.subTest(value=value):
                with self.assertRaises(InvalidExpectedVersionError):
                    service.requeue_notification("missing", expected_version=value)  # type: ignore[arg-type]

    def test_messages_name_the_notification_by_id_only(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        with _LogCapture() as logs:
            with self.assertRaises(NotificationActionError) as raised:
                service.requeue_notification(notification.id, expected_version="stale")

        assert str(notification.id) in str(raised.exception)
        _assert_no_field_values(str(raised.exception))
        _assert_no_field_values(logs.text())

    def test_an_unsupported_error_from_the_write_names_only_the_options_passed(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        cases = [
            ({}, None),
            ({"template_version": 4}, "template_version"),
            ({"expected_version": "v"}, "expected_version"),
        ]

        for options, named in cases:
            with self.subTest(options=options):
                with patch.object(
                    self.backend,
                    "requeue_notification",
                    side_effect=UnsupportedNotificationActionError("unsupported"),
                ):
                    with self.assertRaises(NotificationActionError) as raised:
                        service.requeue_notification(notification.id, send=False, **options)

                assert (raised.exception.reason, raised.exception.action) == (
                    "UNSUPPORTED",
                    "requeue",
                )
                message = str(raised.exception)
                for option in ("template_version", "expected_version"):
                    assert (option in message) == (option == named)


class RequeueWithTemplateVersionTestCase(_SyncCase):
    def test_pins_and_requeues_in_one_backend_write(self):
        service = self.build_service()
        notification = self.make(service, FAILED, send_after=_future())

        with (
            patch.object(
                self.backend,
                "persist_notification_update",
                wraps=self.backend.persist_notification_update,
            ) as updates,
            patch.object(
                self.backend, "requeue_notification", wraps=self.backend.requeue_notification
            ) as requeues,
        ):
            requeued = service.requeue_notification(notification.id, template_version=4)

        assert requeues.call_count == 1
        assert requeues.call_args.kwargs["template_version"] == 4
        updates.assert_not_called()
        assert requeued.status == PENDING
        assert requeued.requested_template_version == 4

    def test_keeps_the_send_time_unless_one_is_given(self):
        service = self.build_service()
        scheduled = _future()
        notification = self.make(service, FAILED, send_after=scheduled)

        requeued = service.requeue_notification(notification.id, template_version=4)

        assert requeued.send_after == scheduled
        assert _sent(service) == []

    def test_sends_a_due_notification_on_the_new_pin(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(
            notification.id, send_after=None, template_version=4
        )

        assert requeued.status == SENT
        assert requeued.requested_template_version == 4
        assert [n.requested_template_version for n, _, _ in _sent(service)] == [4]

    def test_with_a_matching_version(self):
        service = self.build_service()
        notification = self.make(service, CANCELLED)

        requeued = service.requeue_notification(
            notification.id,
            template_version=4,
            expected_version=self.token(notification.id),
            send=False,
        )

        assert (requeued.status, requeued.requested_template_version) == (PENDING, 4)

    def test_a_changed_notification_is_neither_pinned_nor_requeued(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        listed = self.token(notification.id)
        self.backend.store_git_commit_sha(notification.id, "a" * 40)
        before = _stored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(
                notification.id, template_version=4, expected_version=listed
            )

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(notification.id, before)

    def test_repin_then_requeue_still_works(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        service.repin_notification(notification.id, 4)
        requeued = service.requeue_notification(notification.id, send=False)

        assert (requeued.status, requeued.requested_template_version) == (PENDING, 4)

    def test_an_invalid_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        from vintasend.exceptions import InvalidTemplateVersionError

        for value in (-1, 1.5, "3", True):
            with self.subTest(value=value):
                with self.assertRaises(InvalidTemplateVersionError):
                    service.requeue_notification("missing", template_version=value)  # type: ignore[arg-type]


class RequeueReplicationTestCase(_SyncCase):
    def test_a_replica_that_cannot_pin_in_one_write_is_repinned_then_requeued(self):
        replica = _new_backend(_DecliningReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)
        for backend in (self.backend, replica):
            _move_to(backend, created.id, FAILED)

        service.requeue_notification(created.id, template_version=4, send=False)

        stored = replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)

    def test_a_replica_that_can_pin_takes_it_in_the_same_write(self):
        replica = _new_backend(_PinningReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)
        for backend in (self.backend, replica):
            _move_to(backend, created.id, FAILED)

        with patch.object(
            replica, "persist_notification_update", wraps=replica.persist_notification_update
        ) as updates:
            service.requeue_notification(created.id, template_version=4, send=False)

        updates.assert_not_called()
        stored = replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)

    def test_replicas_never_receive_the_expected_version(self):
        replica = _new_backend(_PinningReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)
        for backend in (self.backend, replica):
            _move_to(backend, created.id, FAILED)

        with patch.object(
            replica, "requeue_notification", wraps=replica.requeue_notification
        ) as requeues:
            service.requeue_notification(
                created.id, expected_version=self.token(created.id), send=False
            )

        assert "expected_version" not in requeues.call_args.kwargs
        assert replica.get_notification(created.id).status == PENDING


class RequeueSendOutcomeTestCase(_SyncCase):
    def test_returns_failed_when_the_adapter_fails_and_failures_are_not_raised(self):
        service = self.build_service(raise_on_failed_send=False)
        notification = self.make(service, FAILED)
        adapter = next(iter(service.notification_adapters))

        with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
            requeued = service.requeue_notification(notification.id, send_after=None)

        assert requeued.status == FAILED

    def test_returns_pending_when_handed_to_a_queue(self):
        service = self.build_service(
            adapter=SYNC_BACKGROUND_ADAPTER, notification_queue_service=FakeQueueService()
        )
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(notification.id, send_after=None)

        assert requeued.status == PENDING

    def test_returns_pending_when_scheduled(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        assert service.requeue_notification(notification.id, send_after=_future()).status == (
            PENDING
        )

    def test_send_false_leaves_a_due_notification_for_the_pending_sender(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        requeued = service.requeue_notification(notification.id, send_after=_past(), send=False)

        assert requeued.status == PENDING
        assert _sent(service) == []
        assert [n.id for n in self.backend.get_all_pending_notifications()] == [notification.id]

    def test_a_service_with_no_adapters_can_requeue_without_sending(self):
        building = self.build_service()
        notification = self.make(building, FAILED)
        service = NotificationService(notification_adapters=[], notification_backend=self.backend)

        requeued = service.requeue_notification(notification.id, send_after=None, send=False)

        assert requeued.status == PENDING

    def test_returns_sent_for_a_one_off_notification(self):
        service = self.build_service()
        created = service.create_one_off_notification(**_one_off_kwargs())
        _move_to(self.backend, created.id, FAILED)

        assert service.requeue_notification(created.id, send_after=None).status == SENT


class ConditionalRepinTestCase(_SyncCase):
    def test_pins_when_the_version_matches(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        repinned = service.repin_notification(
            notification.id, 4, expected_version=self.token(notification.id)
        )

        assert repinned.requested_template_version == 4

    def test_a_changed_notification_is_refused_and_nothing_is_written(self):
        service = self.build_service()
        notification = self.make(service, PENDING)
        listed = self.token(notification.id)
        self.backend.store_git_commit_sha(notification.id, "b" * 40)
        before = _stored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.repin_notification(notification.id, 4, expected_version=listed)

        assert (raised.exception.reason, raised.exception.action) == ("CHANGED", "repin")
        self.assert_unchanged(notification.id, before)

    def test_an_expected_version_is_checked_even_when_already_pinned(self):
        service = self.build_service()
        notification = self.make(service, FAILED, requested_template_version=4)
        listed = self.token(notification.id)
        self.backend.store_git_commit_sha(notification.id, "c" * 40)

        with self.assertRaises(NotificationActionError) as raised:
            service.repin_notification(notification.id, 4, expected_version=listed)

        assert raised.exception.reason == "CHANGED"

    def test_already_pinned_with_a_matching_version_writes_nothing(self):
        service = self.build_service()
        notification = self.make(service, FAILED, requested_template_version=4)
        before = _stored(self.backend, notification.id)

        service.repin_notification(notification.id, 4, expected_version=before[1])

        self.assert_unchanged(notification.id, before)

    def test_a_delivered_notification_is_refused_by_the_backend_too(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        before = _stored(self.backend, notification.id)

        with patch.object(
            self.backend, "repin_notification", side_effect=NotificationRepinError("sent")
        ):
            with self.assertRaises(NotificationActionError) as raised:
                service.repin_notification(notification.id, 4)

        assert raised.exception.reason == "INVALID_STATUS"
        self.assert_unchanged(notification.id, before)

    def test_an_unsupported_expected_version_is_refused_before_anything_is_read(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        notification = self.make(service, FAILED)
        before = _stored(self.backend, notification.id)

        with patch.object(
            self.backend, "get_notification", wraps=self.backend.get_notification
        ) as reads:
            with self.assertRaises(NotificationActionError) as raised:
                service.repin_notification(notification.id, 4, expected_version="v")

        assert raised.exception.reason == "UNSUPPORTED"
        reads.assert_not_called()
        self.assert_unchanged(notification.id, before)

    def test_a_backend_without_repin_is_pinned_through_the_generic_update(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        notification = self.make(service, FAILED)

        with patch.object(
            self.backend,
            "persist_notification_update",
            wraps=self.backend.persist_notification_update,
        ) as updates:
            repinned = service.repin_notification(notification.id, 4)

        assert repinned.requested_template_version == 4
        assert updates.call_count == 1

    def test_replicas_take_the_pin_unconditionally(self):
        replica = _new_backend(_DecliningReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)

        service.repin_notification(created.id, 4, expected_version=self.token(created.id))

        assert replica.get_notification(created.id).requested_template_version == 4

    def test_an_unsupported_error_from_the_write_names_expected_version_only_if_passed(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        for passes_expected_version in (False, True):
            with self.subTest(passes_expected_version=passes_expected_version):
                options = (
                    {"expected_version": self.token(notification.id)}
                    if passes_expected_version
                    else {}
                )
                with patch.object(
                    self.backend,
                    "repin_notification",
                    side_effect=UnsupportedNotificationActionError("unsupported"),
                ):
                    with self.assertRaises(NotificationActionError) as raised:
                        service.repin_notification(notification.id, 4, **options)

                assert (raised.exception.reason, raised.exception.action) == (
                    "UNSUPPORTED",
                    "repin",
                )
                assert ("expected_version" in str(raised.exception)) is passes_expected_version


class CurrentTemplateVersionTestCase(_SyncCase):
    def test_reports_what_the_renderer_reports(self):
        service = self.build_service(renderer=VERSIONED_RENDERER)
        assert service.get_current_template_version("EMAIL", "welcome") == 7

    def test_none_for_a_renderer_without_versions_or_one_that_raises(self):
        for renderer in (PLAIN_RENDERER, RAISING_RENDERER):
            with self.subTest(renderer=renderer):
                service = self.build_service(renderer=renderer)
                assert service.get_current_template_version("EMAIL", "welcome") is None

    def test_creation_pins_and_repin_use_it(self):
        service = self.build_service(renderer=VERSIONED_RENDERER, pin_template_versions=True)
        with patch.object(
            NotificationService, "get_current_template_version", return_value=11
        ) as current:
            created = service.create_notification(**_create_kwargs())
            # Read now: the fake hands back its live object, which the repin below changes.
            pinned_at_creation = created.requested_template_version
            _move_to(self.backend, created.id, FAILED)
            repinned = service.repin_notification(created.id)
        # Creation reads the renderer through the same lookup without the public method, so
        # only the repin shows up here, and creation still pinned the renderer's answer.
        assert current.call_count == 1
        assert pinned_at_creation == 7
        assert repinned.requested_template_version == 11


class NotificationVersionsTestCase(_SyncCase):
    def test_lists_ids_and_versions_ordered_by_id(self):
        service = self.build_service()
        created = [service.create_notification(**_create_kwargs()) for _ in range(3)]

        versions = service.filter_notification_versions({}, 1, 10)

        assert [v.id for v in versions] == sorted(str(n.id) for n in created)
        assert all(isinstance(v, NotificationVersion) and v.version for v in versions)

    def test_pages_are_one_indexed(self):
        service = self.build_service()
        for _ in range(3):
            service.create_notification(**_create_kwargs())

        first = service.filter_notification_versions({}, 1, 2)
        second = service.filter_notification_versions({}, 2, 2)

        assert len(first) == 2 and len(second) == 1
        assert {v.id for v in first}.isdisjoint({v.id for v in second})

    def test_the_version_changes_on_every_write(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs())
        first = self.token(created.id)
        assert self.token(created.id) == first

        _move_to(self.backend, created.id, FAILED)

        assert self.token(created.id) != first

    def test_template_version_writes_change_the_token(self):
        # Frozen, so ``modified`` stays put and only the template version field can move it.
        writes = {
            "repin": lambda id_: self.backend.repin_notification(id_, 4),
            "store_template_version": lambda id_: self.backend.store_template_version(id_, 4),
            "update": lambda id_: self.backend.persist_notification_update(
                id_, {"requested_template_version": 5}
            ),
        }
        with freeze_time(FROZEN_AT):
            service = self.build_service()
            for name, write in writes.items():
                with self.subTest(write=name):
                    created = service.create_notification(**_create_kwargs())
                    modified = self.backend.get_notification(created.id).modified
                    first = self.token(created.id)

                    write(created.id)

                    assert self.backend.get_notification(created.id).modified == modified
                    assert self.token(created.id) != first

    def test_int_ids_are_ordered_numerically(self):
        service = self.build_service()
        seeded = self.backend.persist_notification(**_create_kwargs())
        self.backend.notifications = [dataclasses.replace(seeded, id=i) for i in (10, 2)]

        assert [v.id for v in service.filter_notification_versions({}, 1, 10)] == [2, 10]

    def test_mixed_id_types_are_listed(self):
        service = self.build_service()
        regular = service.create_notification(**_create_kwargs())
        one_off = service.create_one_off_notification(**_one_off_kwargs())

        versions = service.filter_notification_versions({}, 1, 10)

        assert {str(v.id) for v in versions} == {str(regular.id), str(one_off.id)}

    def test_a_backend_without_it_raises(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        with self.assertRaises(UnsupportedNotificationActionError):
            service.filter_notification_versions({}, 1, 10)

    def test_reads_a_named_backend(self):
        replica = _new_backend(FakeFileBackend)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = service.create_notification(**_create_kwargs())

        assert [v.id for v in service.filter_notification_versions({}, 1, 10, "backend-1")] == [
            created.id
        ]

    def test_the_stranded_notifications_filter_runs(self):
        service = self.build_service()
        pinned_old = self.make(
            service, PENDING, body_template="welcome", requested_template_version=3
        )
        self.make(service, PENDING, body_template="welcome", requested_template_version=4)
        self.make(service, PENDING, body_template="welcome")  # unpinned
        # Pending, pinned to v3 and unscheduled. Seeded through the backend, because the
        # service would send it at creation and the status leaf would leave it out instead.
        unscheduled = self.backend.persist_notification(
            **_create_kwargs(body_template="welcome", send_after=None, requested_template_version=3)
        )
        assert unscheduled.status == PENDING
        assert matches_filter(unscheduled, {"status": NotificationStatus.PENDING_SEND})
        assert matches_filter(unscheduled, {"has_requested_template_version": True})
        assert matches_filter(unscheduled, {"not": {"requested_template_version": 4}})
        assert not matches_filter(unscheduled, {"has_send_after": True})
        failed = self.make(service, FAILED, body_template="welcome")
        self.make(service, FAILED, body_template="goodbye")

        versions = service.filter_notification_versions(STRANDED_FILTER, 1, 100)

        assert {v.id for v in versions} == {pinned_old.id, failed.id}

    def test_relisting_after_moves_from_page_one(self):
        service = self.build_service()
        for _ in range(3):
            self.make(service, FAILED, body_template="welcome")
        stranded: NotificationFilter = {"status": NotificationStatus.FAILED}

        moved = 0
        while page := service.filter_notification_versions(stranded, 1, 2):
            for entry in page:
                service.requeue_notification(
                    entry.id, template_version=4, expected_version=entry.version, send=False
                )
                moved += 1

        assert moved == 3


class PresenceFilterTestCase(TestCase):
    def _notification(self, **values: Any) -> Any:
        from vintasend.services.dataclasses import Notification

        defaults: dict[str, Any] = {
            "id": "n",
            "user_id": 1,
            "notification_type": "EMAIL",
            "title": "t",
            "body_template": "welcome",
            "context_name": "c",
            "context_kwargs": {},
            "send_after": None,
            "subject_template": "s",
            "preheader_template": "p",
            "status": PENDING,
        }
        defaults.update(values)
        return Notification(**defaults)

    def test_true_matches_set_and_false_matches_none(self):
        scheduled = self._notification(send_after=_future(), requested_template_version=2)
        unscheduled = self._notification()
        for field in ("has_send_after", "has_requested_template_version"):
            with self.subTest(field=field):
                assert matches_filter(scheduled, {field: True})  # type: ignore[misc]
                assert not matches_filter(unscheduled, {field: True})  # type: ignore[misc]
                assert matches_filter(unscheduled, {field: False})  # type: ignore[misc]
                assert not matches_filter(scheduled, {field: False})  # type: ignore[misc]
                assert matches_filter(unscheduled, {"not": {field: True}})  # type: ignore[misc]

    def test_a_non_bool_matches_nothing(self):
        notification = self._notification(send_after=_future())
        for value in (1, "true", None):
            with self.subTest(value=value):
                assert not matches_filter(notification, {"has_send_after": value})  # type: ignore[typeddict-item]

    def test_pinned_but_not_to_a_version(self):
        pinned_filter: NotificationFilter = {
            "and": [
                {"has_requested_template_version": True},
                {"not": {"requested_template_version": 4}},
            ]
        }
        assert matches_filter(self._notification(requested_template_version=3), pinned_filter)
        assert not matches_filter(self._notification(requested_template_version=4), pinned_filter)
        assert not matches_filter(self._notification(), pinned_filter)


class FakeBackendMovesTestCase(_SyncCase):
    """The fake's own contract, called directly as a downstream backend's tests would."""

    def test_requeue_refuses_a_stale_version(self):
        service = self.build_service()
        notification = self.make(service, FAILED)
        with self.assertRaises(NotificationVersionChangedError):
            self.backend.requeue_notification(notification.id, expected_version="stale")

    def test_repin_refuses_a_delivered_notification(self):
        service = self.build_service()
        notification = self.make(service, SENT)
        with self.assertRaises(NotificationRepinError):
            self.backend.repin_notification(notification.id, 4)

    def test_the_base_repin_refuses_an_expected_version(self):
        self.use_backend(_NoActionsBackend)
        service = self.build_service()
        notification = self.make(service, FAILED)
        with self.assertRaises(UnsupportedNotificationActionError):
            self.backend.repin_notification(notification.id, 4, expected_version="v")


# --- AsyncIO ------------------------------------------------------------------------------


class _AsyncCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = _new_backend(FakeAsyncIOFileBackend)
        self._backends: list[Any] = [self.backend]

    async def asyncTearDown(self):
        for backend in self._backends:
            await backend.clear()

    def use_backend(self, cls: type) -> Any:
        self.backend = _new_backend(cls)
        self._backends.append(self.backend)
        return self.backend

    def build_service(
        self, adapter: str = ASYNCIO_ADAPTER, renderer: str = PLAIN_RENDERER, **kwargs: Any
    ) -> AsyncIONotificationService:
        kwargs.setdefault("notification_adapters", [(adapter, renderer)])
        kwargs.setdefault("notification_backend", self.backend)
        return AsyncIONotificationService(**kwargs)

    async def make(self, service: AsyncIONotificationService, status: str, **overrides: Any) -> Any:
        notification = await service.create_notification(**_create_kwargs(**overrides))
        await _amove_to(self.backend, notification.id, status)
        return await self.backend.get_notification(notification.id)

    async def token(self, notification_id: Any) -> str:
        versions = await self.backend.filter_notification_versions({}, 1, 1000)
        return {str(v.id): v.version for v in versions}[str(notification_id)]


class AsyncIOConditionalMovesTestCase(_AsyncCase):
    async def test_capabilities(self):
        assert await self.build_service().get_backend_supported_action_capabilities() == {
            key: True for key in DEFAULT_BACKEND_ACTION_CAPABILITIES
        }
        self.use_backend(_AsyncIONoActionsBackend)
        capabilities = await self.build_service().get_backend_supported_action_capabilities()
        assert capabilities == {key: False for key in DEFAULT_BACKEND_ACTION_CAPABILITIES}

    async def test_requeue_with_a_matching_version_and_a_pin(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        requeued = await service.requeue_notification(
            notification.id,
            template_version=4,
            expected_version=await self.token(notification.id),
            send=False,
        )

        assert (requeued.status, requeued.requested_template_version) == (PENDING, 4)

    async def test_requeue_refuses_a_changed_notification_and_writes_nothing(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        listed = await self.token(notification.id)
        await self.backend.store_git_commit_sha(notification.id, "d" * 40)
        before = await _astored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.requeue_notification(
                notification.id, template_version=4, expected_version=listed
            )

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, notification.id) == before

    async def test_unsupported_options_are_refused_and_nothing_is_written(self):
        self.use_backend(_AsyncIONoActionsBackend)
        service = self.build_service()
        notification = await self.make(service, FAILED)
        before = await _astored(self.backend, notification.id)

        for call in (
            service.requeue_notification(notification.id, expected_version="v"),
            service.requeue_notification(notification.id, template_version=3),
            service.repin_notification(notification.id, 3, expected_version="v"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                await call
            assert raised.exception.reason == "UNSUPPORTED"
        assert await _astored(self.backend, notification.id) == before

    async def test_requeue_returns_the_send_outcome(self):
        service = self.build_service(raise_on_failed_send=False)
        notification = await self.make(service, FAILED)
        adapter = next(iter(service.notification_adapters))

        with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
            failed = await service.requeue_notification(notification.id, send_after=None)
        assert failed.status == FAILED

        sent = await service.requeue_notification(notification.id, send_after=None)
        assert sent.status == SENT

    async def test_requeue_through_a_queue_returns_pending(self):
        service = self.build_service(
            adapter=ASYNCIO_BACKGROUND_ADAPTER,
            notification_queue_service=FakeAsyncIOQueueService(),
        )
        notification = await self.make(service, FAILED)

        assert (await service.requeue_notification(notification.id, send_after=None)).status == (
            PENDING
        )

    async def test_send_false_with_no_adapters(self):
        notification = await self.make(self.build_service(), FAILED)
        service: AsyncIONotificationService = AsyncIONotificationService(
            notification_adapters=[], notification_backend=self.backend
        )

        requeued = await service.requeue_notification(notification.id, send_after=None, send=False)

        assert requeued.status == PENDING

    async def test_repin_refuses_a_changed_notification_and_writes_nothing(self):
        service = self.build_service()
        notification = await self.make(service, CANCELLED)
        listed = await self.token(notification.id)
        await self.backend.store_git_commit_sha(notification.id, "e" * 40)
        before = await _astored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.repin_notification(notification.id, 4, expected_version=listed)

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, notification.id) == before

    async def test_repin_with_a_matching_version(self):
        service = self.build_service()
        notification = await self.make(service, PENDING)

        repinned = await service.repin_notification(
            notification.id, 4, expected_version=await self.token(notification.id)
        )

        assert repinned.requested_template_version == 4

    async def test_repin_through_a_backend_without_its_own_repin(self):
        self.use_backend(_AsyncIONoActionsBackend)
        service = self.build_service()
        notification = await self.make(service, FAILED)

        assert (
            await service.repin_notification(notification.id, 4)
        ).requested_template_version == 4

    async def test_current_template_version_and_versions_listing(self):
        service = self.build_service(renderer=VERSIONED_RENDERER)
        assert await service.get_current_template_version("EMAIL", "welcome") == 7

        pinned_old = await self.make(
            service, PENDING, body_template="welcome", requested_template_version=3
        )
        failed = await self.make(service, FAILED, body_template="welcome")
        await self.make(service, PENDING, body_template="welcome", requested_template_version=4)

        versions = await service.filter_notification_versions(STRANDED_FILTER, 1, 100)

        assert {v.id for v in versions} == {pinned_old.id, failed.id}

    async def test_versions_listing_on_a_backend_without_it_raises(self):
        self.use_backend(_AsyncIONoActionsBackend)
        with self.assertRaises(UnsupportedNotificationActionError):
            await self.build_service().filter_notification_versions({}, 1, 10)


class AsyncIOActionCapabilitiesTestCase(_AsyncCase):
    async def test_a_key_whose_method_is_inherited_reads_false(self):
        self.use_backend(_AsyncIOOverclaimingBackend)
        capabilities = await self.build_service().get_backend_supported_action_capabilities()
        assert capabilities["repin.expectedVersion"] is False
        assert capabilities["versions.filter"] is False
        assert capabilities["requeue.expectedVersion"] is True


class AsyncIOConditionalRequeueTestCase(_AsyncCase):
    async def test_a_status_refusal_wins_over_a_matching_version(self):
        service = self.build_service()
        notification = await self.make(service, SENT)
        before = await _astored(self.backend, notification.id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.requeue_notification(
                notification.id, expected_version=await self.token(notification.id)
            )

        assert raised.exception.reason == "INVALID_STATUS"
        assert await _astored(self.backend, notification.id) == before

    async def test_a_change_between_the_read_and_the_write_is_refused(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        before = await _astored(self.backend, notification.id)

        with patch.object(
            self.backend,
            "requeue_notification",
            side_effect=NotificationVersionChangedError("changed"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                await service.requeue_notification(
                    notification.id, expected_version=await self.token(notification.id)
                )

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, notification.id) == before

    async def test_unsupported_options_are_refused_before_anything_is_read(self):
        self.use_backend(_AsyncIONoActionsBackend)
        service = self.build_service()
        notification = await self.make(service, FAILED)
        before = await _astored(self.backend, notification.id)
        calls = {
            "requeue expected_version": lambda: service.requeue_notification(
                notification.id, expected_version="v"
            ),
            "requeue template_version": lambda: service.requeue_notification(
                notification.id, template_version=3
            ),
            "repin expected_version": lambda: service.repin_notification(
                notification.id, 3, expected_version="v"
            ),
        }

        for name, call in calls.items():
            with self.subTest(call=name):
                with patch.object(
                    self.backend, "get_notification", wraps=self.backend.get_notification
                ) as reads:
                    with self.assertRaises(NotificationActionError) as raised:
                        await call()
                assert raised.exception.reason == "UNSUPPORTED"
                reads.assert_not_called()
                reads.assert_not_awaited()
                assert await _astored(self.backend, notification.id) == before

        # The same backend still requeues without the options.
        requeued = await service.requeue_notification(notification.id, send=False)
        assert requeued.status == PENDING

    async def test_an_invalid_expected_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        for value in ("", 3, True):
            with self.subTest(value=value):
                with self.assertRaises(InvalidExpectedVersionError):
                    await service.requeue_notification("missing", expected_version=value)

    async def test_an_invalid_template_version_is_refused_before_anything_is_read(self):
        service = self.build_service()
        for value in (-1, 1.5, "3", True):
            with self.subTest(value=value):
                with self.assertRaises(InvalidTemplateVersionError):
                    await service.requeue_notification("missing", template_version=value)

    async def test_messages_name_the_notification_by_id_only(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        with _LogCapture() as logs:
            with self.assertRaises(NotificationActionError) as raised:
                await service.requeue_notification(notification.id, expected_version="stale")

        assert str(notification.id) in str(raised.exception)
        _assert_no_field_values(str(raised.exception))
        _assert_no_field_values(logs.text())

    async def test_an_unsupported_error_from_the_write_names_only_the_options_passed(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        cases = [
            ({}, None),
            ({"template_version": 4}, "template_version"),
            ({"expected_version": "v"}, "expected_version"),
        ]

        for options, named in cases:
            with self.subTest(options=options):
                with patch.object(
                    self.backend,
                    "requeue_notification",
                    side_effect=UnsupportedNotificationActionError("unsupported"),
                ):
                    with self.assertRaises(NotificationActionError) as raised:
                        await service.requeue_notification(notification.id, send=False, **options)

                assert (raised.exception.reason, raised.exception.action) == (
                    "UNSUPPORTED",
                    "requeue",
                )
                message = str(raised.exception)
                for option in ("template_version", "expected_version"):
                    assert (option in message) == (option == named)


class AsyncIORequeueReplicationTestCase(_AsyncCase):
    async def _replicated_failed(self, replica_cls: type) -> tuple[Any, Any, Any]:
        replica = self.use_replica(replica_cls)
        service = self.build_service(additional_backends=[replica])
        created = await service.create_notification(**_create_kwargs())
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica, created)
        for backend in (self.backend, replica):
            await _amove_to(backend, created.id, FAILED)
        return service, replica, created

    def use_replica(self, cls: type) -> Any:
        replica = _new_backend(cls)
        self._backends.append(replica)
        return replica

    async def test_a_replica_that_cannot_pin_in_one_write_is_repinned_then_requeued(self):
        service, replica, created = await self._replicated_failed(_AsyncIODecliningReplica)

        with patch.object(
            replica, "persist_notification_update", wraps=replica.persist_notification_update
        ) as updates:
            await service.requeue_notification(created.id, template_version=4, send=False)

        assert updates.await_count == 1
        stored = await replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)

    async def test_a_replica_that_can_pin_takes_it_in_the_same_write(self):
        service, replica, created = await self._replicated_failed(_AsyncIOPinningReplica)

        with patch.object(
            replica, "persist_notification_update", wraps=replica.persist_notification_update
        ) as updates:
            await service.requeue_notification(created.id, template_version=4, send=False)

        updates.assert_not_called()
        stored = await replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)

    async def test_replicas_never_receive_the_expected_version(self):
        service, replica, created = await self._replicated_failed(_AsyncIOPinningReplica)

        with patch.object(
            replica, "requeue_notification", wraps=replica.requeue_notification
        ) as requeues:
            await service.requeue_notification(
                created.id, expected_version=await self.token(created.id), send=False
            )

        assert "expected_version" not in requeues.call_args.kwargs
        assert (await replica.get_notification(created.id)).status == PENDING


class AsyncIOConditionalRepinTestCase(_AsyncCase):
    async def test_a_delivered_notification_is_refused_by_the_backend_too(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        before = await _astored(self.backend, notification.id)

        with patch.object(
            self.backend, "repin_notification", side_effect=NotificationRepinError("sent")
        ):
            with self.assertRaises(NotificationActionError) as raised:
                await service.repin_notification(notification.id, 4)

        assert raised.exception.reason == "INVALID_STATUS"
        assert await _astored(self.backend, notification.id) == before

    async def test_a_change_reported_by_the_backend_is_refused(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        before = await _astored(self.backend, notification.id)

        with patch.object(
            self.backend,
            "repin_notification",
            side_effect=NotificationVersionChangedError("changed"),
        ):
            with self.assertRaises(NotificationActionError) as raised:
                await service.repin_notification(
                    notification.id, 4, expected_version=await self.token(notification.id)
                )

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, notification.id) == before

    async def test_replicas_take_the_pin_unconditionally(self):
        replica = _new_backend(_AsyncIODecliningReplica)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        created = await service.create_notification(**_create_kwargs())
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica, created)

        await service.repin_notification(
            created.id, 4, expected_version=await self.token(created.id)
        )

        assert (await replica.get_notification(created.id)).requested_template_version == 4

    async def test_an_unsupported_error_from_the_write_names_expected_version_only_if_passed(
        self,
    ):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        for passes_expected_version in (False, True):
            with self.subTest(passes_expected_version=passes_expected_version):
                options = (
                    {"expected_version": await self.token(notification.id)}
                    if passes_expected_version
                    else {}
                )
                with patch.object(
                    self.backend,
                    "repin_notification",
                    side_effect=UnsupportedNotificationActionError("unsupported"),
                ):
                    with self.assertRaises(NotificationActionError) as raised:
                        await service.repin_notification(notification.id, 4, **options)

                assert (raised.exception.reason, raised.exception.action) == (
                    "UNSUPPORTED",
                    "repin",
                )
                assert ("expected_version" in str(raised.exception)) is passes_expected_version


class AsyncIONotificationVersionsTestCase(_AsyncCase):
    async def test_the_version_changes_on_every_write(self):
        service = self.build_service()
        created = await service.create_notification(**_create_kwargs())
        first = await self.token(created.id)
        assert await self.token(created.id) == first

        await _amove_to(self.backend, created.id, FAILED)

        assert await self.token(created.id) != first

    async def test_template_version_writes_change_the_token(self):
        writes = {
            "repin": lambda id_: self.backend.repin_notification(id_, 4),
            "store_template_version": lambda id_: self.backend.store_template_version(id_, 4),
            "update": lambda id_: self.backend.persist_notification_update(
                id_, {"requested_template_version": 5}
            ),
        }
        with freeze_time(FROZEN_AT):
            service = self.build_service()
            for name, write in writes.items():
                with self.subTest(write=name):
                    created = await service.create_notification(**_create_kwargs())
                    modified = (await self.backend.get_notification(created.id)).modified
                    first = await self.token(created.id)

                    await write(created.id)

                    assert (await self.backend.get_notification(created.id)).modified == modified
                    assert await self.token(created.id) != first

    async def test_pages_are_one_indexed(self):
        service = self.build_service()
        for _ in range(3):
            await service.create_notification(**_create_kwargs())

        first = await service.filter_notification_versions({}, 1, 2)
        second = await service.filter_notification_versions({}, 2, 2)

        assert len(first) == 2 and len(second) == 1
        assert {v.id for v in first}.isdisjoint({v.id for v in second})

    async def test_int_ids_are_ordered_numerically(self):
        service = self.build_service()
        seeded = await self.backend.persist_notification(**_create_kwargs())
        self.backend.notifications = [dataclasses.replace(seeded, id=i) for i in (10, 2)]

        versions = await service.filter_notification_versions({}, 1, 10)

        assert [v.id for v in versions] == [2, 10]


class AsyncIOFakeBackendMovesTestCase(_AsyncCase):
    """The AsyncIO fake's own contract, called directly as a downstream backend's tests would."""

    async def test_requeue_refuses_a_stale_version(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)
        with self.assertRaises(NotificationVersionChangedError):
            await self.backend.requeue_notification(notification.id, expected_version="stale")

    async def test_repin_refuses_a_delivered_notification(self):
        service = self.build_service()
        notification = await self.make(service, SENT)
        with self.assertRaises(NotificationRepinError):
            await self.backend.repin_notification(notification.id, 4)

    async def test_the_base_repin_refuses_an_expected_version(self):
        self.use_backend(_AsyncIONoActionsBackend)
        service = self.build_service()
        notification = await self.make(service, FAILED)
        with self.assertRaises(UnsupportedNotificationActionError):
            await self.backend.repin_notification(notification.id, 4, expected_version="v")
