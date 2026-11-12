"""Conditional repin and requeue, requeue with a pin, the send outcome, and version listing.

Each refusal is checked to write nothing: the notification is read back from the backend
and compared, version token included, with what it was before the call.
"""

import dataclasses
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

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
    NotificationUpdateError,
    NotificationVersionChangedError,
    UnsupportedNotificationActionError,
)
from vintasend.services.dataclasses import (
    ApplyResult,
    NotificationAttachment,
    NotificationVersion,
)
from vintasend.services.git_commit_sha_providers.stubs.fake_git_commit_sha_provider import (
    FakeAsyncIOGitCommitShaProvider,
    FakeGitCommitShaProvider,
)
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
from vintasend.services.notification_queue_services.stubs.fake_replication_queue_service import (
    FakeAsyncIOReplicationQueueService,
    FakeReplicationQueueService,
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
    SERVICE_LOGGER,
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
READ = NotificationStatus.READ.value

# The capability keys describing an option of a move, which a named backend reads from the
# primary.
_MOVE_OPTION_KEYS = ("repin.expectedVersion", "requeue.expectedVersion", "requeue.templateVersion")

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


class _AsyncIOStrictReplica(_AsyncIODecliningReplica):
    """The AsyncIO twin of ``_StrictReplica``."""

    async def persist_notification_update(  # type: ignore[override]
        self, notification_id: Any, update_data: Any, lock: Any = None
    ) -> Any:
        return await super().persist_notification_update(notification_id, update_data, lock)

    async def mark_pending_as_sent(self, notification_id: Any, lock: Any = None) -> Any:  # type: ignore[override]
        return await super().mark_pending_as_sent(notification_id, lock)

    async def mark_pending_as_failed(self, notification_id: Any, lock: Any = None) -> Any:  # type: ignore[override]
        return await super().mark_pending_as_failed(notification_id, lock)

    async def mark_sent_as_read(self, notification_id: Any, lock: Any = None) -> Any:  # type: ignore[override]
        return await super().mark_sent_as_read(notification_id, lock)

    async def mark_sent_as_read_bulk(  # type: ignore[override]
        self, notification_ids: Any, user_id: Any = None, lock: Any = None
    ) -> Any:
        return await super().mark_sent_as_read_bulk(notification_ids, user_id, lock)

    async def cancel_notification(self, notification_id: Any, lock: Any = None) -> None:  # type: ignore[override]
        await super().cancel_notification(notification_id, lock)

    async def store_context_used(  # type: ignore[override]
        self, notification_id: Any, context: Any, adapter: Any, lock: Any = None
    ) -> None:
        await super().store_context_used(notification_id, context, adapter, lock)

    async def store_git_commit_sha(  # type: ignore[override]
        self, notification_id: Any, git_commit_sha: Any, lock: Any = None
    ) -> None:
        await super().store_git_commit_sha(notification_id, git_commit_sha, lock)

    async def store_template_version(  # type: ignore[override]
        self, notification_id: Any, template_version: Any, lock: Any = None
    ) -> None:
        await super().store_template_version(notification_id, template_version, lock)


class _TokenTakingReplica(FakeFileBackend):
    """A replica without snapshot application that takes the primary's token on its writes."""

    apply_replication_snapshot_if_newer = (
        BaseNotificationBackend.apply_replication_snapshot_if_newer
    )


class _AsyncIOTokenTakingReplica(FakeAsyncIOFileBackend):
    """The AsyncIO twin of ``_TokenTakingReplica``."""

    apply_replication_snapshot_if_newer = (
        AsyncIOBaseNotificationBackend.apply_replication_snapshot_if_newer
    )


_DELIVERED = (NotificationStatus.SENT.value, NotificationStatus.READ.value)


class _DeliveryStrictReplica(_TokenTakingReplica):
    """Refuses a content update on a delivered notification, as the Django and SQLAlchemy
    backends do. The fakes accept it, which would hide a replay that sends one. The empty,
    token-only stamp is accepted in any status, as the ``writes.notificationVersion`` contract
    requires."""

    def persist_notification_update(  # type: ignore[override]
        self, notification_id: Any, update_data: Any, *, notification_version: Any = None
    ) -> Any:
        if update_data and self.get_notification(notification_id).status in _DELIVERED:
            raise NotificationUpdateError(f"Notification {notification_id} was delivered")
        return super().persist_notification_update(
            notification_id, update_data, notification_version=notification_version
        )


class _AsyncIODeliveryStrictReplica(_AsyncIOTokenTakingReplica):
    """The AsyncIO twin of ``_DeliveryStrictReplica``."""

    async def persist_notification_update(  # type: ignore[override]
        self, notification_id: Any, update_data: Any, *, notification_version: Any = None
    ) -> Any:
        if update_data and (await self.get_notification(notification_id)).status in _DELIVERED:
            raise NotificationUpdateError(f"Notification {notification_id} was delivered")
        return await super().persist_notification_update(
            notification_id, update_data, notification_version=notification_version
        )


def _without_token_writes(capabilities: dict[str, bool]) -> dict[str, bool]:
    return {**capabilities, "writes.notificationVersion": False}


class _ReplayingReplica(_TokenTakingReplica):
    """Neither applies snapshots nor takes tokens: it replays writes and issues its own."""

    def get_action_capabilities(self) -> dict[str, bool]:
        return _without_token_writes(super().get_action_capabilities())


class _AsyncIOReplayingReplica(_AsyncIOTokenTakingReplica):
    """The AsyncIO twin of ``_ReplayingReplica``."""

    async def get_action_capabilities(self) -> dict[str, bool]:
        return _without_token_writes(await super().get_action_capabilities())


class _SnapshotOnlyReplica(FakeFileBackend):
    """Applies snapshots but takes no token on the writes it replays when it declines one."""

    def get_action_capabilities(self) -> dict[str, bool]:
        return _without_token_writes(super().get_action_capabilities())


class _AsyncIOSnapshotOnlyReplica(FakeAsyncIOFileBackend):
    """The AsyncIO twin of ``_SnapshotOnlyReplica``."""

    async def get_action_capabilities(self) -> dict[str, bool]:
        return _without_token_writes(await super().get_action_capabilities())


class _StrictReplica(_DecliningReplica):
    """Declines snapshots, reports no actions, and its writes take no ``notification_version``.

    Like a backend written before 3.8.0: any keyword it does not declare raises ``TypeError``.
    The ignores are against FakeFileBackend's widened signatures.
    """

    def persist_notification_update(self, notification_id: Any, update_data: Any) -> Any:  # type: ignore[override]
        return super().persist_notification_update(notification_id, update_data)

    def mark_pending_as_sent(self, notification_id: Any) -> Any:  # type: ignore[override]
        return super().mark_pending_as_sent(notification_id)

    def mark_pending_as_failed(self, notification_id: Any) -> Any:  # type: ignore[override]
        return super().mark_pending_as_failed(notification_id)

    def mark_sent_as_read(self, notification_id: Any) -> Any:  # type: ignore[override]
        return super().mark_sent_as_read(notification_id)

    def mark_sent_as_read_bulk(self, notification_ids: Any, user_id: Any = None) -> Any:  # type: ignore[override]
        return super().mark_sent_as_read_bulk(notification_ids, user_id)

    def cancel_notification(self, notification_id: Any) -> None:  # type: ignore[override]
        super().cancel_notification(notification_id)

    def store_context_used(self, notification_id: Any, context: Any, adapter: Any) -> None:  # type: ignore[override]
        super().store_context_used(notification_id, context, adapter)

    def store_git_commit_sha(self, notification_id: Any, git_commit_sha: Any) -> None:  # type: ignore[override]
        super().store_git_commit_sha(notification_id, git_commit_sha)

    def store_template_version(self, notification_id: Any, template_version: Any) -> None:  # type: ignore[override]
        super().store_template_version(notification_id, template_version)


class _VersionlessTokenTakingReplica(_TokenTakingReplica):
    """Takes the primary's token but inherits the base ``store_template_version``, which has
    nowhere to keep the value and takes no ``notification_version``."""

    store_template_version = BaseNotificationBackend.store_template_version  # type: ignore[assignment]


class _AsyncIOVersionlessTokenTakingReplica(_AsyncIOTokenTakingReplica):
    """The AsyncIO twin of ``_VersionlessTokenTakingReplica``."""

    store_template_version = AsyncIOBaseNotificationBackend.store_template_version  # type: ignore[assignment]


class _TokenClaimingStrictBackend(_StrictReplica):
    """Reports ``writes.notificationVersion`` while no write of it takes the keyword, so any
    write handed a token raises ``TypeError``."""

    def get_action_capabilities(self) -> dict[str, bool]:
        return {"writes.notificationVersion": True}


class _AsyncIOTokenClaimingStrictBackend(_AsyncIOStrictReplica):
    """The AsyncIO twin of ``_TokenClaimingStrictBackend``."""

    async def get_action_capabilities(self) -> dict[str, bool]:
        return {"writes.notificationVersion": True}


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
        # The move options are the primary's, since moves run there; version listing is the
        # named backend's own.
        replica = _new_backend(_NoActionsBackend)
        self._backends.append(replica)
        service = self.build_service(additional_backends=[replica])
        assert service.get_backend_supported_action_capabilities("backend-1") == {
            "repin.expectedVersion": True,
            "requeue.expectedVersion": True,
            "requeue.templateVersion": True,
            "versions.filter": False,
            "writes.notificationVersion": False,
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

        writes = Mock()
        with (
            patch.object(
                replica, "persist_notification_update", wraps=replica.persist_notification_update
            ) as updates,
            patch.object(
                replica, "requeue_notification", wraps=replica.requeue_notification
            ) as requeues,
        ):
            writes.attach_mock(updates, "update")
            writes.attach_mock(requeues, "requeue")
            service.requeue_notification(created.id, template_version=4, send=False)

        # The pin travels on the requeue itself. The updates after it are the convergence and
        # the token-only stamp a replica taking the primary's token gets.
        first_write = writes.mock_calls[0]
        assert first_write[0] == "requeue"
        assert first_write.kwargs["template_version"] == 4
        stored = replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)
        assert stored.notification_version == self.token(created.id)

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


class _ReplicatedSyncCase(_SyncCase):
    def build_replicated(
        self, replica_cls: type = FakeFileBackend, **kwargs: Any
    ) -> tuple[NotificationService, Any]:
        replica = _new_backend(replica_cls)
        self._backends.append(replica)
        return self.build_service(additional_backends=[replica], **kwargs), replica

    def listed(self, service: NotificationService, backend_identifier: str | None = None) -> Any:
        return {
            str(entry.id): entry.version
            for entry in service.filter_notification_versions({}, 1, 1000, backend_identifier)
        }

    def assert_replica_on_primary_token(
        self, service: NotificationService, replica: Any, notification_id: Any
    ) -> str:
        primary_token = self.listed(service)[str(notification_id)]
        assert self.listed(service, "backend-1")[str(notification_id)] == primary_token
        assert replica.get_notification(notification_id).notification_version == primary_token
        return primary_token


class ReplicatedVersionTokenTestCase(_ReplicatedSyncCase):
    def test_a_version_listed_from_the_replica_moves_the_primary(self):
        service, replica = self.build_replicated()
        created = service.create_notification(**_create_kwargs())
        _move_to(self.backend, created.id, FAILED)
        service.replicate_notification(created.id)
        listed = self.listed(service, "backend-1")[str(created.id)]

        requeued = service.requeue_notification(
            created.id, template_version=4, expected_version=listed, send=False
        )

        assert (requeued.status, requeued.requested_template_version) == (PENDING, 4)
        after_requeue = self.assert_replica_on_primary_token(service, replica, created.id)
        assert after_requeue != listed

        repinned = service.repin_notification(created.id, 5, expected_version=after_requeue)

        assert repinned.requested_template_version == 5
        after_repin = self.assert_replica_on_primary_token(service, replica, created.id)
        assert after_repin != after_requeue
        assert replica.get_notification(created.id).requested_template_version == 5

    def test_each_replicated_write_leaves_the_replica_on_the_primary_token(self):
        service, replica = self.build_replicated(
            renderer=VERSIONED_RENDERER, git_commit_sha_provider=FakeGitCommitShaProvider()
        )
        adapter = next(iter(service.notification_adapters))

        def failed_send(notification_id: Any) -> None:
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                service.send(self.backend.get_notification(notification_id))

        def cancel_then_requeue(notification_id: Any) -> None:
            service.cancel_notification(notification_id)
            service.requeue_notification(notification_id, send=False)

        writes = {
            "update": lambda id_: service.update_notification(id_, title="Changed"),
            # Marks sent, stores the context, the git commit SHA and the template version used.
            "send": lambda id_: service.send(self.backend.get_notification(id_)),
            "failed send": failed_send,
            "cancel": service.cancel_notification,
            "requeue": cancel_then_requeue,
            "repin": lambda id_: service.repin_notification(id_, 4),
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                created = service.create_notification(**_create_kwargs())
                after_create = self.assert_replica_on_primary_token(service, replica, created.id)

                write(created.id)

                after_write = self.assert_replica_on_primary_token(service, replica, created.id)
                assert after_write != after_create

        sent = replica.get_notification(
            next(n.id for n in self.backend.notifications if n.status == SENT)
        )
        assert sent.used_template_version == 7
        assert sent.git_commit_sha is not None

    def test_creates_and_read_marking_leave_the_replica_on_the_primary_token(self):
        service, replica = self.build_replicated()
        one_off = service.create_one_off_notification(**_one_off_kwargs())
        sent = [service.create_notification(**_create_kwargs(send_after=None)) for _ in range(3)]
        for notification in (one_off, *sent):
            self.assert_replica_on_primary_token(service, replica, notification.id)

        service.mark_read(sent[0].id)
        read = service.mark_read_bulk([sent[1].id, sent[2].id], user_id=1)

        assert {str(n.id) for n in read} == {str(sent[1].id), str(sent[2].id)}
        for notification in sent:
            self.assert_replica_on_primary_token(service, replica, notification.id)
            assert replica.get_notification(notification.id).status == READ

    def test_bulk_read_marking_falls_back_for_a_declined_snapshot(self):
        service, replica = self.build_replicated()
        first, second = (
            service.create_notification(**_create_kwargs(send_after=None)) for _ in range(2)
        )
        declined_id = str(second.id)
        apply = replica.apply_replication_snapshot_if_newer

        def decline_second(snapshot: Any) -> ApplyResult:
            if str(snapshot.id) == declined_id:
                return ApplyResult(applied=False, reason="declined for test")
            return apply(snapshot)

        with (
            patch.object(replica, "apply_replication_snapshot_if_newer", decline_second),
            patch.object(
                replica, "mark_sent_as_read_bulk", wraps=replica.mark_sent_as_read_bulk
            ) as bulk,
        ):
            service.mark_read_bulk([first.id, second.id], user_id=1)

        assert [str(i) for i in bulk.call_args.args[0]] == [declined_id]
        self.assert_replica_on_primary_token(service, replica, first.id)
        assert replica.get_notification(second.id).status == READ

    def test_bulk_read_marking_is_replayed_on_a_replica_without_snapshot_apply(self):
        service, replica = self.build_replicated(_ReplayingReplica)
        sent = [service.create_notification(**_create_kwargs(send_after=None)) for _ in range(2)]
        for notification in sent:
            FakeFileBackend.apply_replication_snapshot_if_newer(
                replica, self.backend.get_notification(notification.id)
            )
        ids = [n.id for n in sent]

        with patch.object(
            replica, "mark_sent_as_read_bulk", wraps=replica.mark_sent_as_read_bulk
        ) as bulk:
            service.mark_read_bulk(ids, user_id=1)

        bulk.assert_called_once_with(ids, user_id=1)
        assert {replica.get_notification(i).status for i in ids} == {READ}

    def test_a_lagging_replica_gives_a_version_the_primary_refuses(self):
        service, replica = self.build_replicated(
            replication_mode="queued", replication_queue_service=FakeReplicationQueueService()
        )
        created = service.create_notification(**_create_kwargs())
        _move_to(self.backend, created.id, FAILED)
        service.replicate_notification(created.id)
        self.assert_replica_on_primary_token(service, replica, created.id)

        # Enqueued, not yet applied: the replica still holds the previous version.
        service.repin_notification(created.id, 3)
        stale = self.listed(service, "backend-1")[str(created.id)]
        assert stale != self.listed(service)[str(created.id)]
        before = _stored(self.backend, created.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(created.id, expected_version=stale, send=False)

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(created.id, before)

        # Once the worker drains the queue, a fresh listing from the replica moves it.
        service.replicate_notification(created.id)
        fresh = self.assert_replica_on_primary_token(service, replica, created.id)
        requeued = service.requeue_notification(created.id, expected_version=fresh, send=False)
        assert requeued.status == PENDING

    def test_a_replica_that_declines_a_snapshot_holds_a_token_of_its_own(self):
        service, replica = self.build_replicated(_SnapshotOnlyReplica)
        created = service.create_notification(**_create_kwargs())
        _move_to(self.backend, created.id, FAILED)
        service.replicate_notification(created.id)

        with patch.object(
            replica,
            "apply_replication_snapshot_if_newer",
            return_value=ApplyResult(applied=False, reason="declined for test"),
        ):
            service.repin_notification(created.id, 3)

        replayed = self.listed(service, "backend-1")[str(created.id)]
        assert replica.get_notification(created.id).requested_template_version == 3
        assert replayed != self.listed(service)[str(created.id)]
        before = _stored(self.backend, created.id)

        with self.assertRaises(NotificationActionError) as raised:
            service.repin_notification(created.id, 4, expected_version=replayed)

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(created.id, before)


class NamedBackendCapabilitiesTestCase(_ReplicatedSyncCase):
    def test_a_replica_without_snapshot_apply_cannot_list_versions(self):
        service, _ = self.build_replicated(_ReplayingReplica)

        assert service.get_backend_supported_action_capabilities("backend-1") == {
            key: True for key in _MOVE_OPTION_KEYS
        } | {"versions.filter": False, "writes.notificationVersion": False}
        with self.assertRaises(UnsupportedNotificationActionError) as raised:
            service.filter_notification_versions({}, 1, 10, "backend-1")
        assert str(raised.exception) == (
            "Backend backend-1 cannot list version tokens the primary backend accepts"
        )

    def test_move_options_for_a_named_replica_mirror_the_primary(self):
        self.use_backend(_NoActionsBackend)
        service, _ = self.build_replicated()

        assert service.get_backend_supported_action_capabilities("backend-1") == {
            key: False for key in _MOVE_OPTION_KEYS
        } | {"versions.filter": True, "writes.notificationVersion": True}
        assert service.filter_notification_versions({}, 1, 10, "backend-1") == []

    def test_the_primary_named_explicitly_reads_as_omitted(self):
        self.use_backend(_OverclaimingBackend)
        service, _ = self.build_replicated()
        primary = service.get_primary_backend_identifier()

        assert (
            service.get_backend_supported_action_capabilities(primary)
            == service.get_backend_supported_action_capabilities()
        )
        with self.assertRaises(UnsupportedNotificationActionError) as raised:
            service.filter_notification_versions({}, 1, 10, primary)
        assert "cannot list version tokens" not in str(raised.exception)


class NotificationVersionFieldTestCase(_ReplicatedSyncCase):
    def test_notifications_carry_their_current_token(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs())
        first = self.token(created.id)
        assert created.notification_version == first

        updated = service.update_notification(created.id, title="Changed")

        assert updated.notification_version == self.token(created.id) != first
        assert [n.notification_version for n in self.backend.filter_notifications({}, 1, 10)] == [
            self.token(created.id)
        ]

    def test_it_can_be_passed_as_the_expected_version(self):
        service = self.build_service()
        notification = self.make(service, FAILED)

        repinned = service.repin_notification(
            notification.id,
            4,
            expected_version=service.get_notification(notification.id).notification_version,
        )

        assert repinned.requested_template_version == 4

    def test_a_snapshot_without_a_token_gets_one_minted(self):
        service = self.build_service()
        created = service.create_notification(**_create_kwargs())
        replica = _new_backend(FakeFileBackend)
        self._backends.append(replica)

        replica.apply_replication_snapshot_if_newer(
            dataclasses.replace(created, notification_version=None)
        )

        minted = replica.get_notification(created.id).notification_version
        assert minted is not None and minted != created.notification_version
        assert replica._version_token(created.id) == minted

    def test_the_sync_check_ignores_the_token_of_a_replaying_replica(self):
        service, replica = self.build_replicated(_ReplayingReplica)
        created = service.create_notification(**_create_kwargs())
        FakeFileBackend.apply_replication_snapshot_if_newer(replica, created)

        service.update_notification(created.id, title="Changed")

        assert (
            replica.get_notification(created.id).notification_version
            != self.backend.get_notification(created.id).notification_version
        )
        report = service.verify_notification_sync(created.id)
        assert report["in_sync"] is True
        assert "notification_version" not in {entry["field"] for entry in report["fields"]}


class TokenTakingReplicaTestCase(_ReplicatedSyncCase):
    """A replica that declines snapshots but reports ``writes.notificationVersion``."""

    def seed(self, replica: Any, notification_id: Any) -> None:
        # The fallback cannot create a row with the primary's id, so the row is seeded, with no
        # token so the replica starts on one of its own.
        FakeFileBackend.apply_replication_snapshot_if_newer(
            replica,
            dataclasses.replace(
                self.backend.get_notification(notification_id), notification_version=None
            ),
        )

    def test_a_created_row_it_holds_is_converged_to_the_primary_token(self):
        service, replica = self.build_replicated(_TokenTakingReplica)
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)
        assert replica.get_notification(created.id).notification_version != self.token(created.id)

        service.replicate_notification(created.id)

        self.assert_replica_on_primary_token(service, replica, created.id)

    def test_each_replicated_write_leaves_it_on_the_primary_token(self):
        self.check_each_replicated_write(_TokenTakingReplica)

    def test_a_replica_refusing_updates_on_delivered_rows_still_converges(self):
        self.check_each_replicated_write(_DeliveryStrictReplica)

    def check_each_replicated_write(self, replica_cls: Any) -> None:
        service, replica = self.build_replicated(
            replica_cls,
            renderer=VERSIONED_RENDERER,
            git_commit_sha_provider=FakeGitCommitShaProvider(),
        )
        adapter = next(iter(service.notification_adapters))

        def failed_send(notification_id: Any) -> None:
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                service.send(self.backend.get_notification(notification_id))

        def cancel_then_requeue(notification_id: Any) -> None:
            service.cancel_notification(notification_id)
            service.requeue_notification(notification_id, template_version=4, send=False)

        def send_then_read(notification_id: Any) -> None:
            service.send(self.backend.get_notification(notification_id))
            service.mark_read(notification_id)

        def send_then_bulk_read(notification_id: Any) -> None:
            service.send(self.backend.get_notification(notification_id))
            service.mark_read_bulk([notification_id], user_id=1)

        writes = {
            "update": lambda id_: service.update_notification(id_, title="Changed"),
            # Marks sent, stores the context, the git commit SHA and the template version used.
            "send": lambda id_: service.send(self.backend.get_notification(id_)),
            "failed send": failed_send,
            "cancel": service.cancel_notification,
            "requeue": cancel_then_requeue,
            "repin": lambda id_: service.repin_notification(id_, 4),
            "read": send_then_read,
            "bulk read": send_then_bulk_read,
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                created = service.create_notification(**_create_kwargs())
                self.seed(replica, created.id)

                write(created.id)

                self.assert_replica_on_primary_token(service, replica, created.id)
                primary_row = self.backend.get_notification(created.id)
                replica_row = replica.get_notification(created.id)
                for field in ("status", "title", "requested_template_version"):
                    assert getattr(replica_row, field) == getattr(primary_row, field)
                assert replica_row.used_template_version == primary_row.used_template_version
                assert replica_row.git_commit_sha == primary_row.git_commit_sha

    def test_a_version_listed_from_it_moves_the_primary(self):
        service, replica = self.build_replicated(_TokenTakingReplica)
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)
        service.cancel_notification(created.id)
        listed = self.listed(service, "backend-1")[str(created.id)]

        requeued = service.requeue_notification(
            created.id, template_version=4, expected_version=listed, send=False
        )

        assert requeued.status == PENDING
        self.assert_replica_on_primary_token(service, replica, created.id)

    def test_a_replica_reporting_neither_gets_tokens_of_its_own(self):
        service, replica = self.build_replicated(_ReplayingReplica)
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)

        service.update_notification(created.id, title="Changed")

        assert replica.get_notification(created.id).title == "Changed"
        assert replica._version_token(created.id) != self.token(created.id)
        assert (
            service.get_backend_supported_action_capabilities("backend-1")["versions.filter"]
            is False
        )

    def test_a_replica_not_reporting_it_is_never_passed_the_keyword(self):
        service, replica = self.build_replicated(
            _StrictReplica, git_commit_sha_provider=FakeGitCommitShaProvider()
        )
        adapter = next(iter(service.notification_adapters))
        created = [service.create_notification(**_create_kwargs()) for _ in range(5)]
        for notification in created:
            FakeFileBackend.apply_replication_snapshot_if_newer(
                replica, self.backend.get_notification(notification.id)
            )
        first, second, third, fourth, fifth = (n.id for n in created)

        # Any TypeError from a replica write is logged at ERROR and swallowed. The failed send
        # below is the only ERROR line expected, so every replica write took its arguments.
        with self.assertLogs(SERVICE_LOGGER, level="ERROR") as logs:
            service.update_notification(first, title="Changed")
            service.repin_notification(first, 4)
            service.send(self.backend.get_notification(first))
            service.mark_read(first)
            service.send(self.backend.get_notification(second))
            service.mark_read_bulk([second], user_id=1)
            service.cancel_notification(third)
            service.requeue_notification(third, template_version=5, send=False)
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                service.send(self.backend.get_notification(fourth))
            service.replicate_notification(fifth)

        assert [record.getMessage() for record in logs.records] == [
            f"Failed to send notification {fourth}"
        ]
        statuses = {
            str(id_): replica.get_notification(id_).status for id_ in (first, second, third, fourth)
        }
        assert statuses == {
            str(first): READ,
            str(second): READ,
            str(third): PENDING,
            str(fourth): FAILED,
        }
        assert replica.get_notification(first).title == "Changed"
        assert replica.get_notification(third).requested_template_version == 5


class ReplicaTokenStampTestCase(_ReplicatedSyncCase):
    """A replica holds the primary's token only once its row verifiably matches the primary's."""

    def seed(self, replica: Any, notification_id: Any) -> None:
        # With no token, so the replica starts on one of its own.
        FakeFileBackend.apply_replication_snapshot_if_newer(
            replica,
            dataclasses.replace(
                self.backend.get_notification(notification_id), notification_version=None
            ),
        )

    def build_queued(
        self, replica_cls: type = _TokenTakingReplica, **kwargs: Any
    ) -> tuple[NotificationService, Any]:
        return self.build_replicated(
            replica_cls,
            replication_mode="queued",
            replication_queue_service=FakeReplicationQueueService(),
            **kwargs,
        )

    def assert_replica_version_refused(
        self, service: NotificationService, notification_id: Any
    ) -> None:
        listed = self.listed(service, "backend-1")[str(notification_id)]
        assert listed != self.listed(service)[str(notification_id)]
        before = _stored(self.backend, notification_id)

        with self.assertRaises(NotificationActionError) as raised:
            service.requeue_notification(notification_id, expected_version=listed, send=False)

        assert raised.exception.reason == "CHANGED"
        self.assert_unchanged(notification_id, before)

    def test_a_queued_cancel_takes_the_replica_to_cancelled_before_stamping(self):
        service, replica = self.build_queued()
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)
        service.cancel_notification(created.id)
        assert replica.get_notification(created.id).status == PENDING

        result = service.process_replication(created.id)

        assert result == {"successes": ["backend-1"], "failures": []}
        assert replica.get_notification(created.id).status == CANCELLED
        listed = self.assert_replica_on_primary_token(service, replica, created.id)
        requeued = service.requeue_notification(created.id, expected_version=listed, send=False)
        assert requeued.status == PENDING

    def test_a_queued_requeue_takes_the_replica_back_to_pending_before_stamping(self):
        service, replica = self.build_queued()
        for status in (FAILED, CANCELLED):
            with self.subTest(status=status):
                created = service.create_notification(**_create_kwargs())
                _move_to(self.backend, created.id, status)
                self.seed(replica, created.id)
                service.requeue_notification(created.id, template_version=4, send=False)

                service.process_replication(created.id)

                stored = replica.get_notification(created.id)
                assert (stored.status, stored.requested_template_version) == (PENDING, 4)
                self.assert_replica_on_primary_token(service, replica, created.id)

    def test_a_replay_that_fails_part_way_leaves_the_replica_a_token_of_its_own(self):
        service, replica = self.build_queued()
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)
        service.process_replication(created.id)
        self.assert_replica_on_primary_token(service, replica, created.id)
        service.update_notification(created.id, title="Changed")
        service.cancel_notification(created.id)

        with (
            patch.object(replica, "cancel_notification", side_effect=RuntimeError("down")),
            self.assertLogs(SERVICE_LOGGER, level="ERROR"),
        ):
            result = service.process_replication(created.id)

        assert [failure["backend_identifier"] for failure in result["failures"]] == ["backend-1"]
        stored = replica.get_notification(created.id)
        # The content update landed and the cancel did not.
        assert (stored.status, stored.title) == (PENDING, "Changed")
        self.assert_replica_version_refused(service, created.id)

    def test_a_replica_whose_update_did_not_land_is_not_stamped_on_a_cancel(self):
        service, replica = self.build_replicated(_TokenTakingReplica)
        created = service.create_notification(**_create_kwargs())
        original_title = created.title
        self.seed(replica, created.id)
        service.replicate_notification(created.id)
        self.assert_replica_on_primary_token(service, replica, created.id)
        with (
            patch.object(
                replica,
                "persist_notification_update",
                side_effect=NotificationUpdateError("refused"),
            ),
            self.assertLogs(SERVICE_LOGGER, level="ERROR"),
        ):
            service.update_notification(created.id, title="Changed")
        update = replica.persist_notification_update

        def drop_content(notification_id: Any, update_data: Any, **kwargs: Any) -> Any:
            # Reports success without writing the content: only the token-only stamp lands.
            return update(notification_id, {}, **kwargs)

        with (
            patch.object(replica, "persist_notification_update", drop_content),
            self.assertLogs(SERVICE_LOGGER, level="WARNING") as logs,
        ):
            service.cancel_notification(created.id)

        stored = replica.get_notification(created.id)
        assert (stored.status, stored.title) == (CANCELLED, original_title)
        messages = [record.getMessage() for record in logs.records]
        assert len(messages) == 1
        assert str(created.id) in messages[0]
        assert "Changed" not in messages[0] and original_title not in messages[0]
        self.assert_replica_version_refused(service, created.id)

    def test_a_replica_inheriting_store_template_version_is_converged_and_stamped(self):
        service, replica = self.build_replicated(
            _VersionlessTokenTakingReplica,
            renderer=VERSIONED_RENDERER,
            git_commit_sha_provider=FakeGitCommitShaProvider(),
        )
        created = service.create_notification(**_create_kwargs())
        self.seed(replica, created.id)

        with self.assertNoLogs(SERVICE_LOGGER, level="WARNING"):
            service.send(self.backend.get_notification(created.id))

        self.assert_replica_on_primary_token(service, replica, created.id)
        stored = replica.get_notification(created.id)
        assert stored.status == SENT
        assert stored.used_template_version is None
        assert self.backend.get_notification(created.id).used_template_version == 7

    def test_migrating_onto_the_primary_passes_it_no_token(self):
        self.use_backend(_TokenClaimingStrictBackend)
        service, replica = self.build_replicated()
        created = [service.create_notification(**_create_kwargs()) for _ in range(3)]
        for notification in created:
            replica.get_notification(notification.id)

        result = service.migrate_to_backend(
            service.get_primary_backend_identifier(), 2, source_backend_identifier="backend-1"
        )

        assert result == {"migrated": 3, "failures": []}

    def test_an_older_snapshot_arriving_late_leaves_the_newer_row(self):
        with freeze_time(FROZEN_AT) as frozen:
            service, replica = self.build_replicated(
                git_commit_sha_provider=FakeGitCommitShaProvider()
            )
            created = service.create_notification(**_create_kwargs())
            replicate = service._replicate_inline_one
            late: list[tuple[Any, ...]] = []

            def hold_back(identifier: Any, snapshot: Any, write: Any) -> None:
                # A copy, as a backend reading the row returns a fresh record.
                late.append((identifier, dataclasses.replace(snapshot), write))

            frozen.tick(1)
            with patch.object(service, "_replicate_inline_one", hold_back):
                service.send(self.backend.get_notification(created.id))
            frozen.tick(1)
            service.mark_read(created.id)
            newer = self.assert_replica_on_primary_token(service, replica, created.id)

            assert replica.apply_replication_snapshot_if_newer(late[0][1]) == ApplyResult(
                applied=False, reason="local row is newer than the snapshot", superseded=True
            )
            with patch.object(
                replica, "store_context_used", wraps=replica.store_context_used
            ) as stores:
                for identifier, snapshot, write in late:
                    replicate(identifier, snapshot, write)

        stores.assert_not_called()
        assert replica.get_notification(created.id).status == READ
        assert self.assert_replica_on_primary_token(service, replica, created.id) == newer


class FakeBackendTokenKeywordTestCase(_SyncCase):
    """Every fake write takes ``notification_version`` and leaves the row holding it."""

    def test_each_write_holds_the_given_token(self):
        backend = self.backend
        assert backend.get_action_capabilities()["writes.notificationVersion"] is True
        created = backend.persist_notification(**_create_kwargs(), notification_version="t-0")
        one_off = backend.persist_one_off_notification(
            **_one_off_kwargs(), notification_version="t-1"
        )
        assert (backend._version_token(created.id), backend._version_token(one_off.id)) == (
            "t-0",
            "t-1",
        )
        id_ = created.id
        attached = backend.persist_notification(
            **_create_kwargs(),
            attachments=[NotificationAttachment(file=b"x", filename="a.txt")],
        )
        writes = {
            "update": lambda v: backend.persist_notification_update(
                id_, {"title": "Changed"}, notification_version=v
            ),
            "store context": lambda v: backend.store_context_used(
                id_, {}, "adapter", notification_version=v
            ),
            "store sha": lambda v: backend.store_git_commit_sha(
                id_, "a" * 40, notification_version=v
            ),
            "store template version": lambda v: backend.store_template_version(
                id_, 3, notification_version=v
            ),
            "repin": lambda v: backend.repin_notification(id_, 4, notification_version=v),
            "repin to the same version": lambda v: backend.repin_notification(
                id_, 4, notification_version=v
            ),
            "fail": lambda v: backend.mark_pending_as_failed(id_, notification_version=v),
            "requeue": lambda v: backend.requeue_notification(id_, notification_version=v),
            "cancel": lambda v: backend.cancel_notification(id_, notification_version=v),
            "requeue again": lambda v: backend.requeue_notification(id_, notification_version=v),
            "send": lambda v: backend.mark_pending_as_sent(id_, notification_version=v),
            "read": lambda v: backend.mark_sent_as_read(id_, notification_version=v),
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                write(f"t-{name}")
                assert backend._version_token(id_) == f"t-{name}"
                assert backend.get_notification(id_).notification_version == f"t-{name}"

        attachment_id = backend.get_attachments(attached.id)[0].id
        backend.delete_notification_attachment(attachment_id, notification_version="t-detach")
        assert backend._version_token(attached.id) == "t-detach"

    def test_bulk_read_holds_each_given_token(self):
        backend = self.backend
        rows = [backend.persist_notification(**_create_kwargs()) for _ in range(3)]
        for row in rows:
            backend.mark_pending_as_sent(row.id)
        held, minted, untouched = rows

        backend.mark_sent_as_read_bulk(
            [held.id, minted.id],
            user_id=1,
            notification_versions={held.id: "t-held", untouched.id: "t-ignored"},
        )

        assert backend._version_token(held.id) == "t-held"
        assert backend._version_token(minted.id) not in ("t-held", "t-ignored")
        assert backend._version_token(untouched.id) != "t-ignored"


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

        writes = Mock()
        with (
            patch.object(
                replica, "persist_notification_update", wraps=replica.persist_notification_update
            ) as updates,
            patch.object(
                replica, "requeue_notification", wraps=replica.requeue_notification
            ) as requeues,
        ):
            writes.attach_mock(updates, "update")
            writes.attach_mock(requeues, "requeue")
            await service.requeue_notification(created.id, template_version=4, send=False)

        # See the sync twin: the pin travels on the requeue, then convergence and the stamp.
        first_write = writes.mock_calls[0]
        assert first_write[0] == "requeue"
        assert first_write.kwargs["template_version"] == 4
        stored = await replica.get_notification(created.id)
        assert (stored.status, stored.requested_template_version) == (PENDING, 4)
        assert stored.notification_version == await self.token(created.id)

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


class _ReplicatedAsyncCase(_AsyncCase):
    def build_replicated(
        self, replica_cls: type = FakeAsyncIOFileBackend, **kwargs: Any
    ) -> tuple[AsyncIONotificationService, Any]:
        replica = _new_backend(replica_cls)
        self._backends.append(replica)
        return self.build_service(additional_backends=[replica], **kwargs), replica

    async def listed(
        self, service: AsyncIONotificationService, backend_identifier: str | None = None
    ) -> Any:
        versions = await service.filter_notification_versions({}, 1, 1000, backend_identifier)
        return {str(entry.id): entry.version for entry in versions}

    async def assert_replica_on_primary_token(
        self, service: AsyncIONotificationService, replica: Any, notification_id: Any
    ) -> str:
        primary_token = (await self.listed(service))[str(notification_id)]
        assert (await self.listed(service, "backend-1"))[str(notification_id)] == primary_token
        stored = await replica.get_notification(notification_id)
        assert stored.notification_version == primary_token
        return primary_token


class AsyncIOReplicatedVersionTokenTestCase(_ReplicatedAsyncCase):
    async def test_a_version_listed_from_the_replica_moves_the_primary(self):
        service, replica = self.build_replicated()
        created = await service.create_notification(**_create_kwargs())
        await _amove_to(self.backend, created.id, FAILED)
        await service.replicate_notification(created.id)
        listed = (await self.listed(service, "backend-1"))[str(created.id)]

        requeued = await service.requeue_notification(
            created.id, template_version=4, expected_version=listed, send=False
        )

        assert (requeued.status, requeued.requested_template_version) == (PENDING, 4)
        after_requeue = await self.assert_replica_on_primary_token(service, replica, created.id)
        assert after_requeue != listed

        repinned = await service.repin_notification(created.id, 5, expected_version=after_requeue)

        assert repinned.requested_template_version == 5
        after_repin = await self.assert_replica_on_primary_token(service, replica, created.id)
        assert after_repin != after_requeue
        assert (await replica.get_notification(created.id)).requested_template_version == 5

    async def test_each_replicated_write_leaves_the_replica_on_the_primary_token(self):
        service, replica = self.build_replicated(
            renderer=VERSIONED_RENDERER, git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider()
        )
        adapter = next(iter(service.notification_adapters))

        async def update(notification_id: Any) -> None:
            await service.update_notification(notification_id, title="Changed")

        async def send(notification_id: Any) -> None:
            # Marks sent, stores the context, the git commit SHA and the template version used.
            await service.send(await self.backend.get_notification(notification_id))

        async def failed_send(notification_id: Any) -> None:
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                await service.send(await self.backend.get_notification(notification_id))

        async def cancel(notification_id: Any) -> None:
            await service.cancel_notification(notification_id)

        async def cancel_then_requeue(notification_id: Any) -> None:
            await service.cancel_notification(notification_id)
            await service.requeue_notification(notification_id, send=False)

        async def repin(notification_id: Any) -> None:
            await service.repin_notification(notification_id, 4)

        writes = {
            "update": update,
            "send": send,
            "failed send": failed_send,
            "cancel": cancel,
            "requeue": cancel_then_requeue,
            "repin": repin,
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                created = await service.create_notification(**_create_kwargs())
                after_create = await self.assert_replica_on_primary_token(
                    service, replica, created.id
                )

                await write(created.id)

                after_write = await self.assert_replica_on_primary_token(
                    service, replica, created.id
                )
                assert after_write != after_create

        sent = await replica.get_notification(
            next(n.id for n in self.backend.notifications if n.status == SENT)
        )
        assert sent.used_template_version == 7
        assert sent.git_commit_sha is not None

    async def test_creates_and_read_marking_leave_the_replica_on_the_primary_token(self):
        service, replica = self.build_replicated()
        one_off = await service.create_one_off_notification(**_one_off_kwargs())
        sent = [
            await service.create_notification(**_create_kwargs(send_after=None)) for _ in range(3)
        ]
        for notification in (one_off, *sent):
            await self.assert_replica_on_primary_token(service, replica, notification.id)

        await service.mark_read(sent[0].id)
        read = await service.mark_read_bulk([sent[1].id, sent[2].id], user_id=1)

        assert {str(n.id) for n in read} == {str(sent[1].id), str(sent[2].id)}
        for notification in sent:
            await self.assert_replica_on_primary_token(service, replica, notification.id)
            assert (await replica.get_notification(notification.id)).status == READ

    async def test_bulk_read_marking_falls_back_for_a_declined_snapshot(self):
        service, replica = self.build_replicated()
        first = await service.create_notification(**_create_kwargs(send_after=None))
        second = await service.create_notification(**_create_kwargs(send_after=None))
        declined_id = str(second.id)
        apply = replica.apply_replication_snapshot_if_newer

        async def decline_second(snapshot: Any) -> ApplyResult:
            if str(snapshot.id) == declined_id:
                return ApplyResult(applied=False, reason="declined for test")
            return await apply(snapshot)

        with (
            patch.object(replica, "apply_replication_snapshot_if_newer", decline_second),
            patch.object(
                replica, "mark_sent_as_read_bulk", wraps=replica.mark_sent_as_read_bulk
            ) as bulk,
        ):
            await service.mark_read_bulk([first.id, second.id], user_id=1)

        assert [str(i) for i in bulk.call_args.args[0]] == [declined_id]
        await self.assert_replica_on_primary_token(service, replica, first.id)
        assert (await replica.get_notification(second.id)).status == READ

    async def test_bulk_read_marking_is_replayed_on_a_replica_without_snapshot_apply(self):
        service, replica = self.build_replicated(_AsyncIOReplayingReplica)
        sent = [
            await service.create_notification(**_create_kwargs(send_after=None)) for _ in range(2)
        ]
        for notification in sent:
            await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(
                replica, await self.backend.get_notification(notification.id)
            )
        ids = [n.id for n in sent]

        with patch.object(
            replica, "mark_sent_as_read_bulk", wraps=replica.mark_sent_as_read_bulk
        ) as bulk:
            await service.mark_read_bulk(ids, user_id=1)

        bulk.assert_called_once_with(ids, user_id=1)
        assert {(await replica.get_notification(i)).status for i in ids} == {READ}

    async def test_a_lagging_replica_gives_a_version_the_primary_refuses(self):
        service, replica = self.build_replicated(
            replication_mode="queued",
            replication_queue_service=FakeAsyncIOReplicationQueueService(),
        )
        created = await service.create_notification(**_create_kwargs())
        await _amove_to(self.backend, created.id, FAILED)
        await service.replicate_notification(created.id)
        await self.assert_replica_on_primary_token(service, replica, created.id)

        # Enqueued, not yet applied: the replica still holds the previous version.
        await service.repin_notification(created.id, 3)
        stale = (await self.listed(service, "backend-1"))[str(created.id)]
        assert stale != (await self.listed(service))[str(created.id)]
        before = await _astored(self.backend, created.id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.requeue_notification(created.id, expected_version=stale, send=False)

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, created.id) == before

        # Once the worker drains the queue, a fresh listing from the replica moves it.
        await service.replicate_notification(created.id)
        fresh = await self.assert_replica_on_primary_token(service, replica, created.id)
        requeued = await service.requeue_notification(
            created.id, expected_version=fresh, send=False
        )
        assert requeued.status == PENDING

    async def test_a_replica_that_declines_a_snapshot_holds_a_token_of_its_own(self):
        service, replica = self.build_replicated(_AsyncIOSnapshotOnlyReplica)
        created = await service.create_notification(**_create_kwargs())
        await _amove_to(self.backend, created.id, FAILED)
        await service.replicate_notification(created.id)

        with patch.object(
            replica,
            "apply_replication_snapshot_if_newer",
            return_value=ApplyResult(applied=False, reason="declined for test"),
        ):
            await service.repin_notification(created.id, 3)

        replayed = (await self.listed(service, "backend-1"))[str(created.id)]
        assert (await replica.get_notification(created.id)).requested_template_version == 3
        assert replayed != (await self.listed(service))[str(created.id)]
        before = await _astored(self.backend, created.id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.repin_notification(created.id, 4, expected_version=replayed)

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, created.id) == before


class AsyncIONamedBackendCapabilitiesTestCase(_ReplicatedAsyncCase):
    async def test_a_replica_without_snapshot_apply_cannot_list_versions(self):
        service, _ = self.build_replicated(_AsyncIOReplayingReplica)

        assert await service.get_backend_supported_action_capabilities("backend-1") == {
            key: True for key in _MOVE_OPTION_KEYS
        } | {"versions.filter": False, "writes.notificationVersion": False}
        with self.assertRaises(UnsupportedNotificationActionError) as raised:
            await service.filter_notification_versions({}, 1, 10, "backend-1")
        assert str(raised.exception) == (
            "Backend backend-1 cannot list version tokens the primary backend accepts"
        )

    async def test_move_options_for_a_named_replica_mirror_the_primary(self):
        self.use_backend(_AsyncIONoActionsBackend)
        service, _ = self.build_replicated()

        assert await service.get_backend_supported_action_capabilities("backend-1") == {
            key: False for key in _MOVE_OPTION_KEYS
        } | {"versions.filter": True, "writes.notificationVersion": True}
        assert await service.filter_notification_versions({}, 1, 10, "backend-1") == []

    async def test_the_primary_named_explicitly_reads_as_omitted(self):
        self.use_backend(_AsyncIOOverclaimingBackend)
        service, _ = self.build_replicated()
        primary = service.get_primary_backend_identifier()

        assert (
            await service.get_backend_supported_action_capabilities(primary)
            == await service.get_backend_supported_action_capabilities()
        )
        with self.assertRaises(UnsupportedNotificationActionError) as raised:
            await service.filter_notification_versions({}, 1, 10, primary)
        assert "cannot list version tokens" not in str(raised.exception)


class AsyncIONotificationVersionFieldTestCase(_ReplicatedAsyncCase):
    async def test_notifications_carry_their_current_token(self):
        service = self.build_service()
        created = await service.create_notification(**_create_kwargs())
        first = await self.token(created.id)
        assert created.notification_version == first

        updated = await service.update_notification(created.id, title="Changed")

        current = await self.token(created.id)
        assert updated.notification_version == current != first
        listed = await self.backend.filter_notifications({}, 1, 10)
        assert [n.notification_version for n in listed] == [current]

    async def test_it_can_be_passed_as_the_expected_version(self):
        service = self.build_service()
        notification = await self.make(service, FAILED)

        repinned = await service.repin_notification(
            notification.id,
            4,
            expected_version=(await service.get_notification(notification.id)).notification_version,
        )

        assert repinned.requested_template_version == 4

    async def test_a_snapshot_without_a_token_gets_one_minted(self):
        service = self.build_service()
        created = await service.create_notification(**_create_kwargs())
        replica = _new_backend(FakeAsyncIOFileBackend)
        self._backends.append(replica)

        await replica.apply_replication_snapshot_if_newer(
            dataclasses.replace(created, notification_version=None)
        )

        minted = (await replica.get_notification(created.id)).notification_version
        assert minted is not None and minted != created.notification_version
        assert replica._version_token(created.id) == minted

    async def test_the_sync_check_ignores_the_token_of_a_replaying_replica(self):
        service, replica = self.build_replicated(_AsyncIOReplayingReplica)
        created = await service.create_notification(**_create_kwargs())
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(replica, created)

        await service.update_notification(created.id, title="Changed")

        assert (await replica.get_notification(created.id)).notification_version != (
            await self.backend.get_notification(created.id)
        ).notification_version
        report = await service.verify_notification_sync(created.id)
        assert report["in_sync"] is True
        assert "notification_version" not in {entry["field"] for entry in report["fields"]}


class AsyncIOTokenTakingReplicaTestCase(_ReplicatedAsyncCase):
    """The AsyncIO twin of ``TokenTakingReplicaTestCase``."""

    async def seed(self, replica: Any, notification_id: Any) -> None:
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(
            replica,
            dataclasses.replace(
                await self.backend.get_notification(notification_id), notification_version=None
            ),
        )

    async def test_a_created_row_it_holds_is_converged_to_the_primary_token(self):
        service, replica = self.build_replicated(_AsyncIOTokenTakingReplica)
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)
        seeded = (await replica.get_notification(created.id)).notification_version
        assert seeded != await self.token(created.id)

        await service.replicate_notification(created.id)

        await self.assert_replica_on_primary_token(service, replica, created.id)

    async def test_each_replicated_write_leaves_it_on_the_primary_token(self):
        await self.check_each_replicated_write(_AsyncIOTokenTakingReplica)

    async def test_a_replica_refusing_updates_on_delivered_rows_still_converges(self):
        await self.check_each_replicated_write(_AsyncIODeliveryStrictReplica)

    async def check_each_replicated_write(self, replica_cls: Any) -> None:
        service, replica = self.build_replicated(
            replica_cls,
            renderer=VERSIONED_RENDERER,
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(),
        )
        adapter = next(iter(service.notification_adapters))

        async def update(notification_id: Any) -> None:
            await service.update_notification(notification_id, title="Changed")

        async def send(notification_id: Any) -> None:
            await service.send(await self.backend.get_notification(notification_id))

        async def failed_send(notification_id: Any) -> None:
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                await service.send(await self.backend.get_notification(notification_id))

        async def cancel(notification_id: Any) -> None:
            await service.cancel_notification(notification_id)

        async def cancel_then_requeue(notification_id: Any) -> None:
            await service.cancel_notification(notification_id)
            await service.requeue_notification(notification_id, template_version=4, send=False)

        async def repin(notification_id: Any) -> None:
            await service.repin_notification(notification_id, 4)

        async def send_then_read(notification_id: Any) -> None:
            await send(notification_id)
            await service.mark_read(notification_id)

        async def send_then_bulk_read(notification_id: Any) -> None:
            await send(notification_id)
            await service.mark_read_bulk([notification_id], user_id=1)

        writes = {
            "update": update,
            "send": send,
            "failed send": failed_send,
            "cancel": cancel,
            "requeue": cancel_then_requeue,
            "repin": repin,
            "read": send_then_read,
            "bulk read": send_then_bulk_read,
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                created = await service.create_notification(**_create_kwargs())
                await self.seed(replica, created.id)

                await write(created.id)

                await self.assert_replica_on_primary_token(service, replica, created.id)
                primary_row = await self.backend.get_notification(created.id)
                replica_row = await replica.get_notification(created.id)
                for field in ("status", "title", "requested_template_version"):
                    assert getattr(replica_row, field) == getattr(primary_row, field)
                assert replica_row.used_template_version == primary_row.used_template_version
                assert replica_row.git_commit_sha == primary_row.git_commit_sha

    async def test_a_version_listed_from_it_moves_the_primary(self):
        service, replica = self.build_replicated(_AsyncIOTokenTakingReplica)
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)
        await service.cancel_notification(created.id)
        listed = (await self.listed(service, "backend-1"))[str(created.id)]

        requeued = await service.requeue_notification(
            created.id, template_version=4, expected_version=listed, send=False
        )

        assert requeued.status == PENDING
        await self.assert_replica_on_primary_token(service, replica, created.id)

    async def test_a_replica_reporting_neither_gets_tokens_of_its_own(self):
        service, replica = self.build_replicated(_AsyncIOReplayingReplica)
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)

        await service.update_notification(created.id, title="Changed")

        assert (await replica.get_notification(created.id)).title == "Changed"
        assert replica._version_token(created.id) != await self.token(created.id)
        capabilities = await service.get_backend_supported_action_capabilities("backend-1")
        assert capabilities["versions.filter"] is False

    async def test_a_replica_not_reporting_it_is_never_passed_the_keyword(self):
        service, replica = self.build_replicated(
            _AsyncIOStrictReplica, git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider()
        )
        adapter = next(iter(service.notification_adapters))
        created = [await service.create_notification(**_create_kwargs()) for _ in range(5)]
        for notification in created:
            await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(
                replica, await self.backend.get_notification(notification.id)
            )
        first, second, third, fourth, fifth = (n.id for n in created)

        # See the sync twin: the failed send is the only ERROR line expected.
        with self.assertLogs(SERVICE_LOGGER, level="ERROR") as logs:
            await service.update_notification(first, title="Changed")
            await service.repin_notification(first, 4)
            await service.send(await self.backend.get_notification(first))
            await service.mark_read(first)
            await service.send(await self.backend.get_notification(second))
            await service.mark_read_bulk([second], user_id=1)
            await service.cancel_notification(third)
            await service.requeue_notification(third, template_version=5, send=False)
            with patch.object(adapter, "send", side_effect=RuntimeError("smtp down")):
                await service.send(await self.backend.get_notification(fourth))
            await service.replicate_notification(fifth)

        assert [record.getMessage() for record in logs.records] == [
            f"Failed to send notification {fourth}"
        ]
        statuses = {
            str(id_): (await replica.get_notification(id_)).status
            for id_ in (first, second, third, fourth)
        }
        assert statuses == {
            str(first): READ,
            str(second): READ,
            str(third): PENDING,
            str(fourth): FAILED,
        }
        assert (await replica.get_notification(first)).title == "Changed"
        assert (await replica.get_notification(third)).requested_template_version == 5


class AsyncIOReplicaTokenStampTestCase(_ReplicatedAsyncCase):
    """The AsyncIO twin of ``ReplicaTokenStampTestCase``."""

    async def seed(self, replica: Any, notification_id: Any) -> None:
        await FakeAsyncIOFileBackend.apply_replication_snapshot_if_newer(
            replica,
            dataclasses.replace(
                await self.backend.get_notification(notification_id), notification_version=None
            ),
        )

    def build_queued(
        self, replica_cls: type = _AsyncIOTokenTakingReplica, **kwargs: Any
    ) -> tuple[AsyncIONotificationService, Any]:
        return self.build_replicated(
            replica_cls,
            replication_mode="queued",
            replication_queue_service=FakeAsyncIOReplicationQueueService(),
            **kwargs,
        )

    async def assert_replica_version_refused(
        self, service: AsyncIONotificationService, notification_id: Any
    ) -> None:
        listed = (await self.listed(service, "backend-1"))[str(notification_id)]
        assert listed != (await self.listed(service))[str(notification_id)]
        before = await _astored(self.backend, notification_id)

        with self.assertRaises(NotificationActionError) as raised:
            await service.requeue_notification(notification_id, expected_version=listed, send=False)

        assert raised.exception.reason == "CHANGED"
        assert await _astored(self.backend, notification_id) == before

    async def test_a_queued_cancel_takes_the_replica_to_cancelled_before_stamping(self):
        service, replica = self.build_queued()
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)
        await service.cancel_notification(created.id)
        assert (await replica.get_notification(created.id)).status == PENDING

        result = await service.process_replication(created.id)

        assert result == {"successes": ["backend-1"], "failures": []}
        assert (await replica.get_notification(created.id)).status == CANCELLED
        listed = await self.assert_replica_on_primary_token(service, replica, created.id)
        requeued = await service.requeue_notification(
            created.id, expected_version=listed, send=False
        )
        assert requeued.status == PENDING

    async def test_a_queued_requeue_takes_the_replica_back_to_pending_before_stamping(self):
        service, replica = self.build_queued()
        for status in (FAILED, CANCELLED):
            with self.subTest(status=status):
                created = await service.create_notification(**_create_kwargs())
                await _amove_to(self.backend, created.id, status)
                await self.seed(replica, created.id)
                await service.requeue_notification(created.id, template_version=4, send=False)

                await service.process_replication(created.id)

                stored = await replica.get_notification(created.id)
                assert (stored.status, stored.requested_template_version) == (PENDING, 4)
                await self.assert_replica_on_primary_token(service, replica, created.id)

    async def test_a_replay_that_fails_part_way_leaves_the_replica_a_token_of_its_own(self):
        service, replica = self.build_queued()
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)
        await service.process_replication(created.id)
        await self.assert_replica_on_primary_token(service, replica, created.id)
        await service.update_notification(created.id, title="Changed")
        await service.cancel_notification(created.id)

        with (
            patch.object(replica, "cancel_notification", side_effect=RuntimeError("down")),
            self.assertLogs(SERVICE_LOGGER, level="ERROR"),
        ):
            result = await service.process_replication(created.id)

        assert [failure["backend_identifier"] for failure in result["failures"]] == ["backend-1"]
        stored = await replica.get_notification(created.id)
        assert (stored.status, stored.title) == (PENDING, "Changed")
        await self.assert_replica_version_refused(service, created.id)

    async def test_a_replica_whose_update_did_not_land_is_not_stamped_on_a_cancel(self):
        service, replica = self.build_replicated(_AsyncIOTokenTakingReplica)
        created = await service.create_notification(**_create_kwargs())
        original_title = created.title
        await self.seed(replica, created.id)
        await service.replicate_notification(created.id)
        await self.assert_replica_on_primary_token(service, replica, created.id)
        with (
            patch.object(
                replica,
                "persist_notification_update",
                side_effect=NotificationUpdateError("refused"),
            ),
            self.assertLogs(SERVICE_LOGGER, level="ERROR"),
        ):
            await service.update_notification(created.id, title="Changed")
        update = replica.persist_notification_update

        async def drop_content(notification_id: Any, update_data: Any, **kwargs: Any) -> Any:
            return await update(notification_id, {}, **kwargs)

        with (
            patch.object(replica, "persist_notification_update", drop_content),
            self.assertLogs(SERVICE_LOGGER, level="WARNING") as logs,
        ):
            await service.cancel_notification(created.id)

        stored = await replica.get_notification(created.id)
        assert (stored.status, stored.title) == (CANCELLED, original_title)
        messages = [record.getMessage() for record in logs.records]
        assert len(messages) == 1
        assert str(created.id) in messages[0]
        assert "Changed" not in messages[0] and original_title not in messages[0]
        await self.assert_replica_version_refused(service, created.id)

    async def test_a_replica_inheriting_store_template_version_is_converged_and_stamped(self):
        service, replica = self.build_replicated(
            _AsyncIOVersionlessTokenTakingReplica,
            renderer=VERSIONED_RENDERER,
            git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider(),
        )
        created = await service.create_notification(**_create_kwargs())
        await self.seed(replica, created.id)

        with self.assertNoLogs(SERVICE_LOGGER, level="WARNING"):
            await service.send(await self.backend.get_notification(created.id))

        await self.assert_replica_on_primary_token(service, replica, created.id)
        stored = await replica.get_notification(created.id)
        assert stored.status == SENT
        assert stored.used_template_version is None
        assert (await self.backend.get_notification(created.id)).used_template_version == 7

    async def test_migrating_onto_the_primary_passes_it_no_token(self):
        self.use_backend(_AsyncIOTokenClaimingStrictBackend)
        service, replica = self.build_replicated()
        created = [await service.create_notification(**_create_kwargs()) for _ in range(3)]
        for notification in created:
            await replica.get_notification(notification.id)

        result = await service.migrate_to_backend(
            service.get_primary_backend_identifier(), 2, source_backend_identifier="backend-1"
        )

        assert result == {"migrated": 3, "failures": []}

    async def test_an_older_snapshot_arriving_late_leaves_the_newer_row(self):
        with freeze_time(FROZEN_AT) as frozen:
            service, replica = self.build_replicated(
                git_commit_sha_provider=FakeAsyncIOGitCommitShaProvider()
            )
            created = await service.create_notification(**_create_kwargs())
            replicate = service._replicate_inline_one
            late: list[tuple[Any, ...]] = []

            async def hold_back(identifier: Any, snapshot: Any, write: Any) -> None:
                late.append((identifier, dataclasses.replace(snapshot), write))

            frozen.tick(1)
            with patch.object(service, "_replicate_inline_one", hold_back):
                await service.send(await self.backend.get_notification(created.id))
            frozen.tick(1)
            await service.mark_read(created.id)
            newer = await self.assert_replica_on_primary_token(service, replica, created.id)

            assert await replica.apply_replication_snapshot_if_newer(late[0][1]) == ApplyResult(
                applied=False, reason="local row is newer than the snapshot", superseded=True
            )
            with patch.object(
                replica, "store_context_used", wraps=replica.store_context_used
            ) as stores:
                for identifier, snapshot, write in late:
                    await replicate(identifier, snapshot, write)

        stores.assert_not_called()
        assert (await replica.get_notification(created.id)).status == READ
        assert await self.assert_replica_on_primary_token(service, replica, created.id) == newer


class AsyncIOFakeBackendTokenKeywordTestCase(_AsyncCase):
    """The AsyncIO twin of ``FakeBackendTokenKeywordTestCase``."""

    async def test_each_write_holds_the_given_token(self):
        backend = self.backend
        assert (await backend.get_action_capabilities())["writes.notificationVersion"] is True
        created = await backend.persist_notification(**_create_kwargs(), notification_version="t-0")
        one_off = await backend.persist_one_off_notification(
            **_one_off_kwargs(), notification_version="t-1"
        )
        assert (backend._version_token(created.id), backend._version_token(one_off.id)) == (
            "t-0",
            "t-1",
        )
        id_ = created.id
        attached = await backend.persist_notification(
            **_create_kwargs(),
            attachments=[NotificationAttachment(file=b"x", filename="a.txt")],
        )
        writes = {
            "update": lambda v: backend.persist_notification_update(
                id_, {"title": "Changed"}, notification_version=v
            ),
            "store context": lambda v: backend.store_context_used(
                id_, {}, "adapter", notification_version=v
            ),
            "store sha": lambda v: backend.store_git_commit_sha(
                id_, "a" * 40, notification_version=v
            ),
            "store template version": lambda v: backend.store_template_version(
                id_, 3, notification_version=v
            ),
            "repin": lambda v: backend.repin_notification(id_, 4, notification_version=v),
            "repin to the same version": lambda v: backend.repin_notification(
                id_, 4, notification_version=v
            ),
            "fail": lambda v: backend.mark_pending_as_failed(id_, notification_version=v),
            "requeue": lambda v: backend.requeue_notification(id_, notification_version=v),
            "cancel": lambda v: backend.cancel_notification(id_, notification_version=v),
            "requeue again": lambda v: backend.requeue_notification(id_, notification_version=v),
            "send": lambda v: backend.mark_pending_as_sent(id_, notification_version=v),
            "read": lambda v: backend.mark_sent_as_read(id_, notification_version=v),
        }
        for name, write in writes.items():
            with self.subTest(write=name):
                await write(f"t-{name}")
                assert backend._version_token(id_) == f"t-{name}"
                assert (await backend.get_notification(id_)).notification_version == f"t-{name}"

        attachment_id = (await backend.get_attachments(attached.id))[0].id
        await backend.delete_notification_attachment(attachment_id, notification_version="t-detach")
        assert backend._version_token(attached.id) == "t-detach"

    async def test_bulk_read_holds_each_given_token(self):
        backend = self.backend
        rows = [await backend.persist_notification(**_create_kwargs()) for _ in range(3)]
        for row in rows:
            await backend.mark_pending_as_sent(row.id)
        held, minted, untouched = rows

        await backend.mark_sent_as_read_bulk(
            [held.id, minted.id],
            user_id=1,
            notification_versions={held.id: "t-held", untouched.id: "t-ignored"},
        )

        assert backend._version_token(held.id) == "t-held"
        assert backend._version_token(minted.id) not in ("t-held", "t-ignored")
        assert backend._version_token(untouched.id) != "t-ignored"
