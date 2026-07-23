import asyncio
import datetime
import logging
import sys
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, ClassVar, Coroutine, Generic, Literal, TypeGuard, TypeVar, cast

from vintasend.app_settings import NotificationSettings
from vintasend.services.notification_backends.asyncio_base import AsyncIOBaseNotificationBackend


# `typing.Unpack` landed in 3.11; `typing_extensions` is a hard runtime dependency so the
# fallback is always importable. Use a `sys.version_info` guard rather than try/except
# ImportError: mypy evaluates the former statically, and without it the `Unpack[...]`
# annotations silently degrade to `dict[str, Any]` when type-checking against py310.
if sys.version_info >= (3, 11):
    from typing import Unpack
else:
    from typing_extensions import Unpack

from vintasend.constants import NotificationStatus, NotificationTypes
from vintasend.exceptions import (
    BackendNotFoundError,
    DuplicateBackendIdentifierError,
    DuplicateNotificationAdapterError,
    GitCommitShaReassignmentError,
    NotificationContextGenerationError,
    NotificationError,
    NotificationMarkFailedError,
    NotificationMarkSentError,
    NotificationNotFoundError,
    NotificationQueueServiceMissingError,
    NotificationResendError,
    NotificationSendError,
    NotificationUpdateError,
    TenantReassignmentError,
)
from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
from vintasend.services.attachment_managers.base import BaseAttachmentManager
from vintasend.services.dataclasses import (
    AnyNotificationAttachment,
    Notification,
    NotificationContextDict,
    OneOffNotification,
    UpdateNotificationKwargs,
)
from vintasend.services.git_commit_sha_providers.asyncio_base import (
    AsyncIOBaseGitCommitShaProvider,
)
from vintasend.services.git_commit_sha_providers.base import BaseGitCommitShaProvider
from vintasend.services.helpers import (
    get_asyncio_attachment_manager,
    get_asyncio_git_commit_sha_provider,
    get_asyncio_notification_adapters,
    get_asyncio_notification_backend,
    get_asyncio_notification_queue_service,
    get_attachment_manager,
    get_git_commit_sha_provider,
    get_notification_adapters,
    get_notification_backend,
    get_notification_queue_service,
)
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
from vintasend.services.notification_adapters.asyncio_background_base import (
    AsyncIOBackgroundNotificationAdapter,
)
from vintasend.services.notification_adapters.asyncio_base import AsyncIOBaseNotificationAdapter
from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.asyncio_base import (
    supports_attachments as asyncio_supports_attachments,
)
from vintasend.services.notification_backends.base import (
    BaseNotificationBackend,
    supports_attachments,
)
from vintasend.services.notification_backends.filters import (
    DEFAULT_BACKEND_FILTER_CAPABILITIES,
    NotificationFilter,
    NotificationOrderBy,
)
from vintasend.services.notification_queue_services.asyncio_base import (
    AsyncIOBaseNotificationQueueService,
)
from vintasend.services.notification_queue_services.base import BaseNotificationQueueService
from vintasend.services.service_utils import (
    is_asyncio_context_function,
    is_sync_context_function,
    normalize_git_commit_sha,
    validate_attachments,
    validate_email_or_phone,
)
from vintasend.services.utils import get_class_path
from vintasend.utils.singleton_utils import SingletonMeta


logger = logging.getLogger(__name__)

# Queue delivery is at-least-once, so a worker can be handed the same notification id twice.
# A notification in one of these statuses has already had its outcome decided, and re-sending
# it would mean a duplicate the recipient can see. PENDING_SEND and FAILED are deliberately
# absent: re-enqueueing a failed notification is how a host retries it.
ALREADY_DELIVERED_NOTIFICATION_STATUSES = frozenset(
    {
        NotificationStatus.SENT.value,
        NotificationStatus.READ.value,
        NotificationStatus.CANCELLED.value,
    }
)


# Substrings that mark a replica write failure as a normal, reconcilable replication conflict
# rather than a genuine error. Under a best-effort fan-out with retries, two states are
# expected and self-healing: the replica already holds the row a create is replicating
# ("duplicate"/"unique"/"conflict"/"already exists"), or it lacks the row an update is
# replicating ("not found"/"does not exist"/...). Either way the service flips to the opposite
# operation -- converging the replica to the primary's snapshot -- before giving up. Matched
# case-insensitively against ``str(exc)``; mirrors vintasend-ts's
# ``isLikelyDuplicateReplicationConflict`` and widens it to the missing-row direction the flip
# also needs. Substring matching is deliberately loose: a false positive only triggers one
# extra idempotent reconcile attempt, never data loss.
_REPLICATION_CONFLICT_MARKERS = (
    "duplicate",
    "unique",
    "conflict",
    "already exists",
    "not found",
    "does not exist",
    "no notification",
    "matching query does not exist",
)


def _is_likely_duplicate_replication_conflict(exc: BaseException) -> bool:
    """Whether ``exc`` from a replica write looks like a reconcilable replication conflict.

    See ``_REPLICATION_CONFLICT_MARKERS``: a ``True`` result tells the service the failure is
    the expected create-collides / update-missing race and it should flip to converging the
    replica to the snapshot, rather than logging the write off as a hard failure.
    """
    message = str(exc).lower()
    return any(marker in message for marker in _REPLICATION_CONFLICT_MARKERS)


# The return type of the primary write passed to ``_execute_multi_backend_write``, propagated
# so a create returns a ``Notification`` and a mark returns whatever its backend method does.
_WriteResultT = TypeVar("_WriteResultT")


def validate_unique_adapter_notification_types(
    adapters: Iterable[BaseNotificationAdapter | AsyncIOBaseNotificationAdapter],
) -> None:
    """
    Validate that no two adapters declare the same notification type.

    :param adapters: An iterable of notification adapters.
    :raises DuplicateNotificationAdapterError: If duplicate notification types are found.
    """
    grouped: defaultdict[str, list[str]] = defaultdict(list)
    for adapter in adapters:
        grouped[adapter.notification_type.value].append(adapter.adapter_import_str)

    duplicates = [
        f"{notification_type} ({', '.join(import_strs)})"
        for notification_type, import_strs in grouped.items()
        if len(import_strs) > 1
    ]

    if duplicates:
        raise DuplicateNotificationAdapterError(
            "Duplicate adapter notification types are not allowed. Found duplicates for: "
            + ", ".join(duplicates)
        )


class Contexts(metaclass=SingletonMeta):
    _contexts: ClassVar[
        dict[
            str,
            Callable[[Any], NotificationContextDict]
            | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
        ]
    ] = {}

    def register_function(
        self,
        key: str,
        func: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ):
        self._contexts[key] = func

    def get_function(self, key: str):
        return self._contexts.get(key)


def register_context(key: str):
    def decorator(func: Callable[[Any], NotificationContextDict]):
        contexts = Contexts()
        contexts.register_function(key, func)
        return func

    return decorator


A = TypeVar("A", BaseNotificationAdapter, BackgroundNotificationAdapter)
B = TypeVar("B", bound=BaseNotificationBackend)


class NotificationService(Generic[A, B]):
    notification_adapters: Iterable[A]
    notification_backend: B
    notification_queue_service: BaseNotificationQueueService | None
    raise_on_failed_send: bool
    replication_mode: Literal["inline", "queued"]
    _backends: dict[str, B]
    _primary_backend_identifier: str

    def __init__(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None = None,
        notification_backend: B | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
        notification_queue_service: BaseNotificationQueueService | str | None = None,
        attachment_manager: BaseAttachmentManager | str | None = None,
        git_commit_sha_provider: BaseGitCommitShaProvider | str | None = None,
        raise_on_failed_send: bool = False,
        additional_backends: Iterable[B | str] | None = None,
        replication_mode: Literal["inline", "queued"] = "inline",
    ):
        """
        Build a notification service.

        :param notification_adapters: adapter instances, or (adapter, renderer) import strings.
        :param notification_backend: a backend instance or its import string.
        :param notification_backend_kwargs: kwargs for the backend when it is an import string.
        :param config: the host's config object, used by FastAPI-style apps.
        :param notification_queue_service: the queue service used to hand background
            notifications to a worker. Accepts an instance or an import string; when it is
            None, `NOTIFICATION_QUEUE_SERVICE` is used, and background sending is simply
            unavailable if that is unset too.
        :param git_commit_sha_provider: resolves the git commit SHA recorded on a
            notification at send time. Accepts an instance or an import string; when it is
            None, `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` is used, and no SHA is ever
            resolved or written if that is unset too -- the feature is simply off.
        :param raise_on_failed_send: when False (the default), a failure to send, enqueue, or
            record a notification's outcome is logged and the remaining adapters still run.
            When True, those failures are raised, which is the 1.x behaviour.
        :param additional_backends: extra backends replicated reads (and, from a later
            phase, writes) can be routed to. Each entry is a backend instance or its import
            string, resolved the same way as `notification_backend`. A backend is
            addressed by its `get_backend_identifier()` when it declares one, or by
            `backend-{n}` otherwise -- `n` is the backend's position among the additional
            backends, starting at 1 (the primary is position 0). Absent
            `additional_backends`, the service behaves exactly as a single-backend 2.0
            deployment.
        :param replication_mode: how writes fan out to the additional backends. ``"inline"``
            (the default) replicates on the request path, right after the primary write.
            ``"queued"`` is accepted now for forward compatibility but only wired in a later
            phase; until then it behaves as ``"inline"``. Ignored entirely by a single-backend
            service, which never replicates.
        :raises DuplicateBackendIdentifierError: if an additional backend's resolved
            identifier collides with an already-registered backend's identifier --
            including the primary's.
        """
        # initialize the notification settings singleton for the first time
        # to ensure all components have access to the same settings
        NotificationSettings(config)

        self.raise_on_failed_send = raise_on_failed_send
        self.replication_mode = replication_mode

        if isinstance(notification_queue_service, BaseNotificationQueueService):
            self.notification_queue_service = notification_queue_service
        else:
            try:
                self.notification_queue_service = get_notification_queue_service(
                    notification_queue_service, None, config
                )
            except NotificationQueueServiceMissingError:
                # Nothing configured at all: background sending stays unavailable, which
                # only matters once send() meets a BackgroundNotificationAdapter. A
                # NotificationQueueServiceResolutionError -- configured but unusable, e.g. a
                # typo'd import string -- deliberately propagates instead: swallowing it
                # would read as "no queue configured" and silently never deliver.
                self.notification_queue_service = None

        if isinstance(notification_backend, BaseNotificationBackend):
            self.notification_backend = cast(B, notification_backend)
        else:
            self.notification_backend = cast(
                B,
                get_notification_backend(notification_backend, notification_backend_kwargs, config),
            )
        self.notification_backend_import_str = get_class_path(self.notification_backend)

        # Build the ordered backend registry: the primary first, then every additional
        # backend in the order given. A backend's identifier is whatever
        # `get_backend_identifier()` reports, or `backend-{n}` (n = its position among the
        # additional backends, 1-indexed; the primary falls back to `backend-0`) when it
        # does not declare one. Absent `additional_backends`, this is just `{primary: ...}`
        # and every read below resolves to the primary exactly as in a single-backend 2.0
        # deployment.
        primary_backend_identifier = self.notification_backend.get_backend_identifier() or (
            "backend-0"
        )
        self._backends = {primary_backend_identifier: self.notification_backend}
        self._primary_backend_identifier = primary_backend_identifier

        if additional_backends is not None:
            for index, additional_backend in enumerate(additional_backends, start=1):
                if isinstance(additional_backend, BaseNotificationBackend):
                    resolved_additional_backend = cast(B, additional_backend)
                else:
                    resolved_additional_backend = cast(
                        B, get_notification_backend(additional_backend, None, config)
                    )
                additional_backend_identifier = (
                    resolved_additional_backend.get_backend_identifier() or f"backend-{index}"
                )
                if additional_backend_identifier in self._backends:
                    raise DuplicateBackendIdentifierError(
                        f"Two configured backends resolve to the same identifier "
                        f"'{additional_backend_identifier}'"
                    )
                self._backends[additional_backend_identifier] = resolved_additional_backend

        # Resolve the attachment manager (instance, dotted path, or the
        # NOTIFICATION_ATTACHMENT_MANAGER setting) and inject it into the backend when the
        # backend accepts one. A backend that does not do attachments is left untouched.
        if isinstance(attachment_manager, BaseAttachmentManager):
            self.attachment_manager: BaseAttachmentManager | None = attachment_manager
        else:
            self.attachment_manager = get_attachment_manager(attachment_manager, None, config)
        if self.attachment_manager is not None and supports_attachments(self.notification_backend):
            self.notification_backend.inject_attachment_manager(self.attachment_manager)

        # Resolve the git commit SHA provider (instance, dotted path, or the
        # NOTIFICATION_GIT_COMMIT_SHA_PROVIDER setting). None means the feature is off: no
        # SHA is ever resolved or written -- see _resolve_and_persist_git_commit_sha.
        if isinstance(git_commit_sha_provider, BaseGitCommitShaProvider):
            self.git_commit_sha_provider: BaseGitCommitShaProvider | None = git_commit_sha_provider
        else:
            self.git_commit_sha_provider = get_git_commit_sha_provider(
                git_commit_sha_provider, None, config
            )

        if notification_adapters is None or self._check_is_adapters_tuple_iterable(
            notification_adapters
        ):
            self.notification_adapters = cast(
                Iterable[A],
                get_notification_adapters(
                    notification_adapters,
                    self.notification_backend_import_str,
                    notification_backend_kwargs if notification_backend_kwargs is not None else {},
                    config,
                ),
            )
        elif self._check_is_base_notification_adapter_iterable(notification_adapters):
            self.notification_adapters = notification_adapters
        else:
            raise NotificationError("Invalid notification adapters")

        validate_unique_adapter_notification_types(self.notification_adapters)

        self.notification_adapters_import_strs = [
            (get_class_path(adapter), get_class_path(adapter.template_renderer))
            for adapter in self.notification_adapters
        ]

    def _check_is_base_notification_adapter_iterable(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[A]]:
        return notification_adapters is not None and all(
            isinstance(adapter, BaseNotificationAdapter) for adapter in notification_adapters
        )

    def _check_is_adapters_tuple_iterable(
        self,
        notification_adapters: Iterable[A]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]]:
        return notification_adapters is not None and all(
            (isinstance(adapter, tuple) or isinstance(adapter, list))
            and len(adapter) == 2
            and (
                isinstance(adapter[0], str)
                or (
                    isinstance(adapter[0], tuple)
                    and isinstance(adapter[0][0], str)
                    and isinstance(adapter[0][1], dict)
                )
            )
            and (
                isinstance(adapter[1], str)
                or (
                    isinstance(adapter[1], tuple)
                    and isinstance(adapter[1][0], str)
                    and isinstance(adapter[1][1], dict)
                )
            )
            for adapter in notification_adapters
        )

    def _validate_attachments(
        self, attachments: list[AnyNotificationAttachment]
    ) -> list[AnyNotificationAttachment]:
        """Validate attachments and return the validated list."""
        return validate_attachments(attachments)

    def register_queue_service(self, queue_service: BaseNotificationQueueService) -> None:
        """
        Inject the queue service after construction.

        Useful when the queue service cannot exist yet at construction time -- a broker
        connection built during application startup, for example.

        Parameters:
            queue_service: BaseNotificationQueueService - the queue service to use for
                background sends from now on
        """
        self.notification_queue_service = queue_service

    def get_primary_backend_identifier(self) -> str:
        """Return the primary backend's identifier."""
        return self._primary_backend_identifier

    def get_all_backend_identifiers(self) -> list[str]:
        """Return every registered backend's identifier, primary first, in the order the
        backends were configured."""
        return list(self._backends.keys())

    def get_additional_backend_identifiers(self) -> list[str]:
        """Return every registered backend's identifier except the primary's, in the order
        the additional backends were configured."""
        return [
            identifier
            for identifier in self._backends
            if identifier != self._primary_backend_identifier
        ]

    def has_backend(self, backend_identifier: str) -> bool:
        """Whether `backend_identifier` names a registered backend (primary or additional)."""
        return backend_identifier in self._backends

    def _get_backend(self, backend_identifier: str | None = None) -> B:
        """Resolve a backend by identifier for read routing.

        `None` resolves to the primary backend, preserving every existing call site's
        behaviour. An identifier that names no registered backend raises
        `BackendNotFoundError` rather than silently falling back to the primary.
        """
        if backend_identifier is None:
            return self._backends[self._primary_backend_identifier]
        if backend_identifier not in self._backends:
            raise BackendNotFoundError(
                f"No backend registered with identifier '{backend_identifier}'"
            )
        return self._backends[backend_identifier]

    def _execute_multi_backend_write(
        self,
        primary_write: Callable[[B], _WriteResultT],
        additional_write: Callable[[B, "Notification | OneOffNotification | None"], Any],
        replication_notification_id: int | str | uuid.UUID | None = None,
    ) -> _WriteResultT:
        """Run a write on the primary, then fan it out to every additional backend inline.

        The primary write runs first and its result -- and any exception -- is the caller's:
        the primary is the source of truth, so its failure is the user's failure and
        propagates unchanged. Only after it succeeds, and only when additional backends are
        configured, is the write replicated. Each replica is handled in registry order and any
        replica failure is logged and swallowed, so a rejecting replica never fails the user's
        operation -- it is reconciled by a later write. A single-backend service short-circuits
        and never enters the replication loop, staying byte-for-byte identical to a
        single-backend 2.0 deployment.

        ``primary_write`` performs the mutation on the backend it is handed. ``additional_write``
        applies the same mutation to a replica when snapshot application is unavailable; it
        receives the primary's post-write snapshot (or ``None`` when there is no single record
        to snapshot, e.g. bulk read-marking or a cancel that deleted the row).
        ``replication_notification_id`` names the record to snapshot when the primary write does
        not itself return one.
        """
        result = primary_write(self.notification_backend)

        additional_backend_identifiers = self.get_additional_backend_identifiers()
        if not additional_backend_identifiers:
            return result

        snapshot = self._read_replication_snapshot(replication_notification_id, result)
        for backend_identifier in additional_backend_identifiers:
            replica = self._backends[backend_identifier]
            try:
                self._replicate_write_to_backend(replica, snapshot, additional_write)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to replicate a write to backend %s (notification %s); the primary "
                    "write succeeded and the replica will be reconciled by a later write",
                    backend_identifier,
                    getattr(snapshot, "id", replication_notification_id),
                )
        return result

    def _read_replication_snapshot(
        self,
        replication_notification_id: int | str | uuid.UUID | None,
        result: Any,
    ) -> "Notification | OneOffNotification | None":
        """Re-read the primary's authoritative record to replicate, or ``None`` if there is none.

        Prefers ``replication_notification_id``; otherwise uses the id of the primary write's
        result when that result is itself a notification (creates and updates). Returns ``None``
        when no id resolves (e.g. bulk read-marking) or the record no longer exists on the
        primary (e.g. a cancel that deleted it) -- the caller then relies on ``additional_write``
        instead of snapshot application.

        Runs after the primary write has already committed, so it must never fail the caller:
        any exception raised by the re-read (not just ``NotificationError``, e.g. a transient
        connection error from a real backend) is logged and swallowed, degrading this replica
        pass to the ``additional_write`` fallback rather than failing an already-successful
        primary write.
        """
        snapshot_id = replication_notification_id
        if snapshot_id is None and isinstance(result, (Notification, OneOffNotification)):
            snapshot_id = result.id
        if snapshot_id is None:
            return None
        try:
            return self.notification_backend.get_notification(snapshot_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to re-read notification %s to build a replication snapshot; the "
                "primary write already succeeded and replication will be reconciled later",
                snapshot_id,
                exc_info=True,
            )
            return None

    def _replicate_write_to_backend(
        self,
        replica: B,
        snapshot: "Notification | OneOffNotification | None",
        additional_write: Callable[[B, "Notification | OneOffNotification | None"], Any],
    ) -> None:
        """Apply one write to one replica, preferring snapshot application then falling back.

        When a snapshot is available the replica's ``apply_replication_snapshot_if_newer`` is
        tried first: a backend that implements it upserts the whole record -- creating the row
        with the primary's id or refreshing it newer-wins -- in one call. When it declines
        (``applied=False``, the concrete default) the ``additional_write`` fallback mirrors the
        mutation with the backend's own primitives. A duplicate/conflict failure -- the replica
        already holds a created row, or lacks a row being updated -- flips once to converging the
        replica to the snapshot before the error propagates to the caller's logging.
        """
        try:
            if (
                snapshot is not None
                and replica.apply_replication_snapshot_if_newer(snapshot).applied
            ):
                return
            additional_write(replica, snapshot)
        except Exception as exc:  # noqa: BLE001
            if snapshot is None or not _is_likely_duplicate_replication_conflict(exc):
                raise
            self._converge_replica_to_snapshot(replica, snapshot)

    def _replicate_snapshot_fallback(
        self, replica: B, snapshot: "Notification | OneOffNotification | None"
    ) -> None:
        """Default ``additional_write``: converge a replica to the primary's snapshot.

        Used for every single-record write (create, update, mark, store). It only converges a
        row the replica already holds; it cannot create a row with the primary's id, because no
        base primitive assigns a chosen id -- that is exactly what
        ``apply_replication_snapshot_if_newer`` exists for. A backend that implements snapshot
        application never reaches this path.
        """
        if snapshot is None:
            return
        self._converge_replica_to_snapshot(replica, snapshot)

    def _converge_replica_to_snapshot(
        self, replica: B, snapshot: "Notification | OneOffNotification"
    ) -> None:
        """Bring an existing replica row into line with the primary's snapshot via primitives.

        Serves as both the read-then-write fallback for a backend that declines snapshot
        application and the flip target when a replica reports a duplicate/conflict. Requires the
        row to already exist on the replica; an absent row is logged and skipped, since inline
        replication cannot create a row with the primary's id without snapshot-apply support.
        """
        try:
            replica.get_notification(snapshot.id)
        except NotificationNotFoundError:
            logger.warning(
                "Replica lacks notification %s and does not implement "
                "apply_replication_snapshot_if_newer; cannot create it with its primary id "
                "inline -- it will be reconciled later",
                snapshot.id,
            )
            return
        update_data: UpdateNotificationKwargs = {
            "title": snapshot.title,
            "body_template": snapshot.body_template,
            "context_name": snapshot.context_name,
            "context_kwargs": snapshot.context_kwargs,
            "send_after": snapshot.send_after,
            "subject_template": snapshot.subject_template,
            "preheader_template": snapshot.preheader_template,
            "adapter_extra_parameters": snapshot.adapter_extra_parameters,
        }
        replica.persist_notification_update(notification_id=snapshot.id, update_data=update_data)
        if snapshot.context_used is not None:
            replica.store_context_used(
                snapshot.id, snapshot.context_used, snapshot.adapter_used or ""
            )
        if snapshot.git_commit_sha is not None:
            replica.store_git_commit_sha(snapshot.id, snapshot.git_commit_sha)
        if snapshot.status == NotificationStatus.SENT.value:
            replica.mark_pending_as_sent(snapshot.id)
        elif snapshot.status == NotificationStatus.FAILED.value:
            replica.mark_pending_as_failed(snapshot.id)
        elif snapshot.status == NotificationStatus.READ.value:
            replica.mark_sent_as_read(snapshot.id)

    def _replicate_store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: NotificationContextDict,
        adapter_import_str: str,
    ) -> None:
        """Persist ``context_used`` on the primary and fan it out to the replicas.

        Extracted from ``send`` / ``delayed_send`` into its own method so the
        ``_execute_multi_backend_write`` closure captures these method parameters rather than
        the send loop's ``adapter`` and ``context`` variables -- a loop-captured closure is a
        late-binding hazard the linters (rightly) reject.
        """
        self._execute_multi_backend_write(
            lambda backend: backend.store_context_used(
                notification_id, context, adapter_import_str
            ),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )

    def _resolve_and_persist_git_commit_sha(
        self, notification: Notification | OneOffNotification
    ) -> None:
        """Resolve the current git commit SHA and store it if it differs from what is stored.

        Called at the top of both send() and delayed_send() so foreground and background
        deliveries record the revision that actually handled this execution. A no-op when no
        provider is configured. A provider that raises is caught and logged, then treated
        the same as a None return -- audit metadata is never worth failing a delivery over.
        A non-null but malformed SHA is a configuration error and propagates
        InvalidGitCommitShaError rather than being swallowed.
        """
        if self.git_commit_sha_provider is None:
            return

        try:
            resolved_sha = self.git_commit_sha_provider.get_current_git_commit_sha()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Git commit SHA provider raised while resolving the SHA for notification "
                "%s; treating it as unknown for this execution",
                notification.id,
            )
            return

        if resolved_sha is None:
            return

        normalized_sha = normalize_git_commit_sha(resolved_sha)
        if normalized_sha == notification.git_commit_sha:
            return

        self._execute_multi_backend_write(
            lambda backend: backend.store_git_commit_sha(notification.id, normalized_sha),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification.id,
        )
        notification.git_commit_sha = normalized_sha

    def send(
        self,
        notification: Notification | OneOffNotification,
        context: NotificationContextDict | None = None,
    ) -> None:
        """
        Send a notification using the appropriate adapter.

        Adapters that subclass BackgroundNotificationAdapter are not delivered here. Their
        notification id is handed to the configured queue service and a worker delivers it
        later through delayed_send, so no context is generated and the notification's status
        is left untouched on that path.

        If a git commit SHA provider is configured, this always resolves and (when it
        differs from what is stored) persists the current git commit SHA first, regardless
        of raise_on_failed_send. A malformed, non-null SHA raises InvalidGitCommitShaError.

        With raise_on_failed_send=False (the default) every failure below is logged and the
        remaining adapters still run. With raise_on_failed_send=True this method may raise:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationQueueServiceMissingError if a background adapter has no queue
              service to enqueue through;
            * NotificationSendError if the adapter fails to send the notification, or the
              queue service fails to accept it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification | OneOffNotification - the notification to be sent
            context: NotificationContextDict | None - a pre-computed context to send with,
                bypassing context generation entirely. Used by ``resend_notification`` to reuse
                a stored ``context_used`` verbatim; regular callers leave this ``None`` so
                context is generated as usual.
        """
        self._resolve_and_persist_git_commit_sha(notification)

        # Context is generated lazily below, and only for adapters that deliver in this
        # process: the enqueue branch must not pay for a context the worker will generate
        # again anyway. A caller such as resend_notification may pass a context in, in which
        # case it is used as-is and no generation happens.

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if isinstance(adapter, BackgroundNotificationAdapter):
                if self.notification_queue_service is None:
                    logger.error(
                        "Cannot send notification %s in the background: adapter %s requires a "
                        "queue service and none is configured",
                        notification.id,
                        adapter.adapter_import_str,
                    )
                    if self.raise_on_failed_send:
                        raise NotificationQueueServiceMissingError(
                            "No notification queue service is configured"
                        )
                    continue
                try:
                    self.notification_queue_service.enqueue_notification(notification.id)
                except NotificationError as e:
                    # Queue services are contractually required to wrap broker failures in a
                    # NotificationError subclass. Anything they leave unwrapped escapes here,
                    # as their base class documents.
                    logger.exception("Failed to enqueue notification %s", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationSendError("Failed to enqueue notification") from e
                continue

            if context is None:
                try:
                    context = self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification.id
                    )
                    try:
                        self._execute_multi_backend_write(
                            lambda backend: backend.mark_pending_as_failed(notification.id),
                            self._replicate_snapshot_fallback,
                            replication_notification_id=notification.id,
                        )
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification.id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    # The context belongs to the notification, not to one adapter, so no
                    # other adapter could send it either.
                    return

            try:
                adapter.send(
                    notification=notification,
                    context=context,
                )
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification.id)
                try:
                    self._execute_multi_backend_write(
                        lambda backend: backend.mark_pending_as_failed(notification.id),
                        self._replicate_snapshot_fallback,
                        replication_notification_id=notification.id,
                    )
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                self._execute_multi_backend_write(
                    lambda backend: backend.mark_pending_as_sent(notification.id),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=notification.id,
                )
                self._replicate_store_context_used(
                    notification.id, context, adapter.adapter_import_str
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification.id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

    def create_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> Notification:
        """
        Create a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to send the notification to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[AnyNotificationAttachment] | None - attachments to include
            tenant: str | None - the tenant this notification belongs to. Cannot be changed
                after creation -- see ``update_notification``.
        """
        validated_attachments = self._validate_attachments(attachments or [])

        persist_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "notification_type": notification_type,
            "title": title,
            "body_template": body_template,
            "context_name": context_name,
            "context_kwargs": context_kwargs,
            "send_after": send_after,
            "subject_template": subject_template,
            "preheader_template": preheader_template,
            "adapter_extra_parameters": adapter_extra_parameters,
        }
        # Only pass ``attachments`` when there is something to attach: backends that do
        # not accept the keyword (e.g. attachment-unaware backends) then keep working.
        if validated_attachments:
            persist_kwargs["attachments"] = validated_attachments

        if tenant is not None:
            persist_kwargs["tenant"] = tenant

        notification = self._execute_multi_backend_write(
            lambda backend: backend.persist_notification(**persist_kwargs),
            self._replicate_snapshot_fallback,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def create_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> "OneOffNotification":
        """
        Create a one-off notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * InvalidOneOffNotificationRecipientError if email_or_phone is invalid;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            email_or_phone: str - the email or phone that the one-off notification will be
                sent to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[AnyNotificationAttachment] | None - attachments to include
            tenant: str | None - the tenant this notification belongs to. Cannot be changed
                after creation -- see ``update_notification``.
        """
        validate_email_or_phone(email_or_phone)
        validated_attachments = self._validate_attachments(attachments or [])

        persist_kwargs: dict[str, Any] = {
            "email_or_phone": email_or_phone,
            "first_name": first_name,
            "last_name": last_name,
            "notification_type": notification_type,
            "title": title,
            "body_template": body_template,
            "context_name": context_name,
            "context_kwargs": context_kwargs,
            "send_after": send_after,
            "subject_template": subject_template,
            "preheader_template": preheader_template,
            "adapter_extra_parameters": adapter_extra_parameters,
        }
        if validated_attachments:
            persist_kwargs["attachments"] = validated_attachments

        if tenant is not None:
            persist_kwargs["tenant"] = tenant

        notification = self._execute_multi_backend_write(
            lambda backend: backend.persist_one_off_notification(**persist_kwargs),
            self._replicate_snapshot_fallback,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def update_notification(
        self,
        notification_id: int | str | uuid.UUID,
        **kwargs: Unpack[UpdateNotificationKwargs],
    ) -> Notification | OneOffNotification:
        """
        Update a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * TenantReassignmentError if ``tenant`` is present in kwargs -- a
              notification's tenant cannot be reassigned after creation.
            * GitCommitShaReassignmentError if ``git_commit_sha`` is present in kwargs --
              it is system-managed and only ever written by NotificationService at send
              time.
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to update
            **kwargs: UpdateNotificationKwargs - the fields to update
        """
        if "tenant" in kwargs:
            raise TenantReassignmentError("A notification's tenant cannot be reassigned")
        if "git_commit_sha" in kwargs:
            raise GitCommitShaReassignmentError(
                "A notification's git_commit_sha is system-managed and cannot be set directly"
            )
        notification = self._execute_multi_backend_write(
            lambda backend: backend.persist_notification_update(
                notification_id=notification_id,
                update_data=kwargs,
            ),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(notification)
        return notification

    def get_all_future_notifications(
        self, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return self._get_backend(backend_identifier).get_all_future_notifications()

    def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications from the user
        """
        return self._get_backend(backend_identifier).get_all_future_notifications_from_user(user_id)

    def get_future_notifications_from_user(
        self,
        user_id: int | str | uuid.UUID,
        page: int,
        page_size: int,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of the future notifications from the user
        """
        return self._get_backend(backend_identifier).get_future_notifications_from_user(
            user_id, page, page_size
        )

    def get_future_notifications(
        self, page: int, page_size: int, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return self._get_backend(backend_identifier).get_future_notifications(page, page_size)

    def _is_asyncio_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, NotificationContextDict]]]:
        return is_asyncio_context_function(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return is_sync_context_function(context_function)

    def get_notification_context(
        self, notification: Notification | OneOffNotification
    ) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification | OneOffNotification - the notification to generate the context for
        """
        context_function = Contexts().get_function(notification.context_name)
        if context_function is None:
            raise NotificationContextGenerationError("Context function not found")
        try:
            if self._is_asyncio_context_function(context_function):
                return asyncio.run(context_function(*[], **notification.context_kwargs))
            elif self._is_sync_context_function(context_function):
                return context_function(*[], **notification.context_kwargs)
            raise NotificationContextGenerationError("Invalid context function")
        except Exception as e:  # noqa: BLE001
            raise NotificationContextGenerationError("Failed getting notification context") from e

    def send_pending_notifications(self) -> None:
        """
        Send all pending notifications in the backend.

        This method doesn't raise any exceptions, but it provides specific logs for each
        notification (success or failure) and a summary at the end with the number of notifications
        sent and failed.
        """

        pending_notifications = self.notification_backend.get_all_pending_notifications()
        notifications_sent = 0
        notifications_failed = 0
        for notification in pending_notifications:
            try:
                self.send(notification)
            except NotificationSendError:
                notifications_failed += 1
                logger.exception("Failed to send notification %s", notification.id)
            except NotificationMarkFailedError:
                notifications_failed += 1
                logger.exception("Failed to send notification %s", notification.id)
                logger.exception("Failed to mark notification %s as failed", notification.id)
            except NotificationMarkSentError:
                logger.info("Notification %s sent", notification.id)
                logger.exception("Failed to mark notification %s as sent", notification.id)
                notifications_sent += 1
            else:
                logger.info("Notification %s sent", notification.id)
                notifications_sent += 1

        logger.info("Sent %s notifications", notifications_sent)
        logger.info("Failed to send %s notifications", notifications_failed)

    def get_pending_notifications(
        self, page: int, page_size: int, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the pending notifications
        """
        return self._get_backend(backend_identifier).get_pending_notifications(page, page_size)

    def get_notification(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> Notification | OneOffNotification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Notification | OneOffNotification - the notification
        """
        return self._get_backend(backend_identifier).get_notification(notification_id)

    def mark_read(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to mark as read

        Returns:
            Notification | OneOffNotification - the updated notification
        """
        return self._execute_multi_backend_write(
            lambda backend: backend.mark_sent_as_read(notification_id),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )

    def mark_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> Iterable[Notification]:
        """
        Mark multiple notifications as read at once.

        This is idempotent: ids that are already read, missing, not owned by
        ``user_id`` (when provided), or in a non-SENT state are simply skipped --
        no error is raised. When ``user_id`` is provided the update is scoped to
        that user; passing it is strongly recommended for endpoint use.

        Parameters:
            notification_ids: Iterable[int | str | uuid.UUID] - the notifications to mark as read
            user_id: int | str | uuid.UUID | None - optional owner scope

        Returns:
            Iterable[Notification] - the requested notifications that are read after the operation
        """
        # Materialize once: the id iterable may be a single-use generator, and both the primary
        # write and every replica mirror need to consume it.
        ids = list(notification_ids)
        return self._execute_multi_backend_write(
            lambda backend: backend.mark_sent_as_read_bulk(ids, user_id=user_id),
            lambda backend, snapshot: backend.mark_sent_as_read_bulk(ids, user_id=user_id),
        )

    def get_in_app_unread(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification]:
        """
        Get unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification] - the unread in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self._get_backend(backend_identifier).filter_in_app_unread_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    def get_in_app_notifications(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification]:
        """
        Get all in-app notifications (read + unread) for a user, paginated.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification] - the in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self._get_backend(backend_identifier).filter_in_app_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    def get_in_app_notifications_count(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> int:
        """
        Get the total count of in-app notifications (read + unread) for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self._get_backend(backend_identifier).count_in_app_notifications(user_id)

    def get_in_app_unread_count(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> int:
        """
        Get the total count of unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return self._get_backend(backend_identifier).count_in_app_unread_notifications(user_id)

    def filter_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        page: int,
        page_size: int,
        order_by: NotificationOrderBy | None = None,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Query notifications with a composable filter, ordering and pagination.

        The filter supports ``and`` / ``or`` / ``not`` groups, scalar-or-list membership, string
        lookups and inclusive date ranges. An empty filter (``{}``) matches every notification.
        Both ``Notification`` and ``OneOffNotification`` are returned. See
        ``BaseNotificationBackend.filter_notifications`` for the full semantics.

        Parameters:
            filter: NotificationFilter - the composable filter (``{}`` matches everything)
            page: int - the 1-indexed page number to get
            page_size: int - the number of notifications per page
            order_by: NotificationOrderBy | None - the primary sort field and direction
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of matches
        """
        return self._get_backend(backend_identifier).filter_notifications(
            filter, page=page, page_size=page_size, order_by=order_by
        )

    def count_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        backend_identifier: str | None = None,
    ) -> int:
        """
        Count notifications matching ``filter``, ignoring pagination.

        This is the total a dashboard needs to render pagination; ``filter_notifications``
        returns an ``Iterable`` that cannot be ``len()``-ed.

        Parameters:
            filter: NotificationFilter - the composable filter (``{}`` counts everything)
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            int - the number of matching notifications
        """
        return self._get_backend(backend_identifier).count_notifications(filter)

    def get_backend_supported_filter_capabilities(
        self, backend_identifier: str | None = None
    ) -> dict[str, bool]:
        """
        Report which filter capabilities the configured backend supports.

        The backend declares only what it *cannot* do; this merges that report OVER an
        all-``True`` default, so every capability the backend does not mention comes back
        ``True``. Keys are camelCase dotted (``'fields.notificationType'``, ``'orderBy.sentAt'``),
        kept byte-identical to the TypeScript sibling so one dashboard consumes either.

        Parameters:
            backend_identifier: str | None - which registered backend to report on; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            dict[str, bool] - the merged capability report
        """
        return {
            **DEFAULT_BACKEND_FILTER_CAPABILITIES,
            **self._get_backend(backend_identifier).get_filter_capabilities(),
        }

    def resend_notification(
        self,
        notification_id: int | str | uuid.UUID,
        use_stored_context_if_available: bool = False,
    ) -> Notification:
        """
        Resend a notification by cloning it into a brand-new PENDING row and sending that
        clone immediately. This is the operation a dashboard "retry" action drives.

        The source notification is re-read from the backend and left completely untouched --
        its id, status and timestamps never change. The clone is a new row with its own id,
        carrying over the source's user, template, context and attachment configuration.

        This method may raise the following exceptions:
            * NotificationResendError if the notification is a one-off, or is still scheduled
              in the future (``send_after`` set and not yet due).
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to resend
            use_stored_context_if_available: bool - when True and the source notification has
                a stored ``context_used``, the clone reuses it verbatim instead of regenerating
                context at send time. Defaults to False.

        Returns:
            Notification - the newly created clone
        """
        source = self.notification_backend.get_notification(notification_id)
        if isinstance(source, OneOffNotification):
            raise NotificationResendError("One-off notifications cannot be resent")
        if source.send_after is not None and source.send_after > datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            raise NotificationResendError(
                "Cannot resend a notification that is still scheduled in the future"
            )

        extra_persist_kwargs: dict[str, Any] = {}
        if source.tenant is not None:
            extra_persist_kwargs["tenant"] = source.tenant

        clone = self._execute_multi_backend_write(
            lambda backend: backend.persist_notification(
                user_id=source.user_id,
                notification_type=source.notification_type,
                title=source.title,
                body_template=source.body_template,
                context_name=source.context_name,
                context_kwargs=source.context_kwargs,
                send_after=None,
                subject_template=source.subject_template,
                preheader_template=source.preheader_template,
                adapter_extra_parameters=source.adapter_extra_parameters,
                **extra_persist_kwargs,
            ),
            self._replicate_snapshot_fallback,
        )

        if source.attachments:
            clone = cast(
                Notification,
                self._execute_multi_backend_write(
                    lambda backend: backend.persist_notification_update(
                        notification_id=clone.id,
                        update_data={"attachments": list(source.attachments)},
                    ),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=clone.id,
                ),
            )

        reused_context: NotificationContextDict | None = None
        if use_stored_context_if_available and source.context_used is not None:
            reused_context = cast(NotificationContextDict, source.context_used)

        if clone.send_after is None or clone.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            self.send(clone, context=reused_context)
        return clone

    def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to cancel
        """

        def _cancel_on_replica(
            backend: B, snapshot: "Notification | OneOffNotification | None"
        ) -> None:
            # A cancel deletes the row, so there is no snapshot to apply -- mirror the delete on
            # the replica. A replica that never held the row is already in the desired (absent)
            # state, so a not-found is success, not a failure.
            try:
                backend.cancel_notification(notification_id)
            except NotificationNotFoundError:
                return

        return self._execute_multi_backend_write(
            lambda backend: backend.cancel_notification(notification_id),
            _cancel_on_replica,
            replication_notification_id=notification_id,
        )

    def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Deliver a notification from a background worker, given only its id.

        This is the worker half of the background path: `send()` hands the notification id to
        the queue service, and the worker calls this. The notification is reloaded from the
        backend and its context is generated here, at delivery time, so a scheduled
        notification renders against current data exactly as a foreground send does.

        Delivery is at-least-once, so this may be called twice for the same id. A notification
        that has already been delivered (or cancelled) is skipped rather than sent again.

        With raise_on_failed_send=False (the default) failures are logged rather than raised.
        With raise_on_failed_send=True this method may raise:
            * NotificationNotFoundError if the id does not resolve to a notification;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if no background adapter handles the notification's type,
              or the adapter fails to send it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Note that NotificationNotFoundError propagates regardless of raise_on_failed_send:
        an id that resolves to nothing is a wiring or retention problem, not a failed send.

        If a git commit SHA provider is configured, this always resolves and (when it
        differs from what is stored) persists the current git commit SHA before delivering,
        so the worker records the revision that actually sent the notification. A malformed,
        non-null SHA raises InvalidGitCommitShaError.

        Parameters:
            notification_id: int | str | uuid.UUID - the id of the notification to deliver
        """
        notification = self.notification_backend.get_notification(notification_id)

        if notification.status in ALREADY_DELIVERED_NOTIFICATION_STATUSES:
            logger.info(
                "Skipping background send of notification %s: its status is already %s",
                notification_id,
                notification.status,
            )
            return

        # Resolved here, after the already-delivered check: the SHA records the revision
        # that sends the notification, so a skipped redelivery of an already-sent row must
        # not overwrite it with a later, unrelated revision.
        self._resolve_and_persist_git_commit_sha(notification)

        context: NotificationContextDict | None = None
        background_adapter_found = False

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if not isinstance(adapter, BackgroundNotificationAdapter):
                # A foreground adapter has nothing to do in a worker, but the ones after it
                # in the list might, so keep going.
                continue

            background_adapter_found = True

            if context is None:
                try:
                    context = self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification_id
                    )
                    try:
                        self._execute_multi_backend_write(
                            lambda backend: backend.mark_pending_as_failed(notification_id),
                            self._replicate_snapshot_fallback,
                            replication_notification_id=notification_id,
                        )
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification_id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    return

            try:
                adapter.send(notification=notification, context=context)
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification_id)
                try:
                    self._execute_multi_backend_write(
                        lambda backend: backend.mark_pending_as_failed(notification_id),
                        self._replicate_snapshot_fallback,
                        replication_notification_id=notification_id,
                    )
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification_id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                self._execute_multi_backend_write(
                    lambda backend: backend.mark_pending_as_sent(notification_id),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=notification_id,
                )
                self._replicate_store_context_used(
                    notification_id, context, adapter.adapter_import_str
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification_id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

        if not background_adapter_found:
            logger.error(
                "No background notification adapter is configured for notification %s of type %s",
                notification_id,
                notification.notification_type,
            )
            if self.raise_on_failed_send:
                raise NotificationSendError(
                    "No background notification adapter found for this notification"
                )


AAIO = TypeVar("AAIO", bound=AsyncIOBaseNotificationAdapter)
BAIO = TypeVar("BAIO", bound=AsyncIOBaseNotificationBackend)


class AsyncIONotificationService(Generic[AAIO, BAIO]):
    notification_adapters: Iterable[AAIO]
    notification_backend: BAIO
    notification_queue_service: AsyncIOBaseNotificationQueueService | None
    raise_on_failed_send: bool
    replication_mode: Literal["inline", "queued"]
    _backends: dict[str, BAIO]
    _primary_backend_identifier: str

    def __init__(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None = None,
        notification_backend: BAIO | str | None = None,
        notification_backend_kwargs: dict | None = None,
        config: Any = None,
        notification_queue_service: AsyncIOBaseNotificationQueueService | str | None = None,
        attachment_manager: AsyncIOBaseAttachmentManager | str | None = None,
        git_commit_sha_provider: AsyncIOBaseGitCommitShaProvider | str | None = None,
        raise_on_failed_send: bool = False,
        additional_backends: Iterable[BAIO | str] | None = None,
        replication_mode: Literal["inline", "queued"] = "inline",
    ):
        """
        Build an AsyncIO notification service.

        :param notification_adapters: adapter instances, or (adapter, renderer) import strings.
        :param notification_backend: a backend instance or its import string.
        :param notification_backend_kwargs: kwargs for the backend when it is an import string.
        :param config: the host's config object, used by FastAPI-style apps.
        :param notification_queue_service: the queue service used to hand background
            notifications to a worker. Accepts an instance or an import string; when it is
            None, `NOTIFICATION_QUEUE_SERVICE` is used, and background sending is simply
            unavailable if that is unset too.
        :param git_commit_sha_provider: resolves the git commit SHA recorded on a
            notification at send time. Accepts an instance or an import string; when it is
            None, `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` is used, and no SHA is ever
            resolved or written if that is unset too -- the feature is simply off.
        :param raise_on_failed_send: when False (the default), a failure to send, enqueue, or
            record a notification's outcome is logged and the remaining adapters still run.
            When True, those failures are raised, which is the 1.x behaviour.
        :param additional_backends: extra backends replicated reads (and, from a later
            phase, writes) can be routed to. Each entry is a backend instance or its import
            string, resolved the same way as `notification_backend`. A backend is
            addressed by its `get_backend_identifier()` when it declares one, or by
            `backend-{n}` otherwise -- `n` is the backend's position among the additional
            backends, starting at 1 (the primary is position 0). Absent
            `additional_backends`, the service behaves exactly as a single-backend 2.0
            deployment.
        :param replication_mode: how writes fan out to the additional backends. ``"inline"``
            (the default) replicates on the request path, right after the primary write.
            ``"queued"`` is accepted now for forward compatibility but only wired in a later
            phase; until then it behaves as ``"inline"``. Ignored entirely by a single-backend
            service, which never replicates.
        :raises DuplicateBackendIdentifierError: if an additional backend's resolved
            identifier collides with an already-registered backend's identifier --
            including the primary's.
        """
        # initialize the notification settings singleton for the first time
        # to ensure all components have access to the same settings
        NotificationSettings(config)

        self.raise_on_failed_send = raise_on_failed_send
        self.replication_mode = replication_mode

        if isinstance(notification_queue_service, AsyncIOBaseNotificationQueueService):
            self.notification_queue_service = notification_queue_service
        else:
            try:
                self.notification_queue_service = get_asyncio_notification_queue_service(
                    notification_queue_service, None, config
                )
            except NotificationQueueServiceMissingError:
                # Nothing configured at all: background sending stays unavailable, which
                # only matters once send() meets an AsyncIOBackgroundNotificationAdapter. A
                # NotificationQueueServiceResolutionError -- configured but unusable, e.g. a
                # typo'd import string -- deliberately propagates instead: swallowing it
                # would read as "no queue configured" and silently never deliver.
                self.notification_queue_service = None

        if isinstance(notification_backend, AsyncIOBaseNotificationBackend):
            self.notification_backend = cast(BAIO, notification_backend)
        else:
            self.notification_backend = cast(
                BAIO,
                get_asyncio_notification_backend(
                    notification_backend, notification_backend_kwargs, config
                ),
            )
        self.notification_backend_import_str = get_class_path(self.notification_backend)

        # Build the ordered backend registry: the primary first, then every additional
        # backend in the order given. A backend's identifier is whatever
        # `get_backend_identifier()` reports, or `backend-{n}` (n = its position among the
        # additional backends, 1-indexed; the primary falls back to `backend-0`) when it
        # does not declare one. Absent `additional_backends`, this is just `{primary: ...}`
        # and every read below resolves to the primary exactly as in a single-backend 2.0
        # deployment. `get_backend_identifier` is sync even here, since it needs no I/O and
        # this constructor cannot await.
        primary_backend_identifier = self.notification_backend.get_backend_identifier() or (
            "backend-0"
        )
        self._backends = {primary_backend_identifier: self.notification_backend}
        self._primary_backend_identifier = primary_backend_identifier

        if additional_backends is not None:
            for index, additional_backend in enumerate(additional_backends, start=1):
                if isinstance(additional_backend, AsyncIOBaseNotificationBackend):
                    resolved_additional_backend = cast(BAIO, additional_backend)
                else:
                    resolved_additional_backend = cast(
                        BAIO, get_asyncio_notification_backend(additional_backend, None, config)
                    )
                additional_backend_identifier = (
                    resolved_additional_backend.get_backend_identifier() or f"backend-{index}"
                )
                if additional_backend_identifier in self._backends:
                    raise DuplicateBackendIdentifierError(
                        f"Two configured backends resolve to the same identifier "
                        f"'{additional_backend_identifier}'"
                    )
                self._backends[additional_backend_identifier] = resolved_additional_backend

        # Resolve the attachment manager (instance, dotted path, or the
        # NOTIFICATION_ATTACHMENT_MANAGER setting) and inject it into the backend when the
        # backend accepts one. A backend that does not do attachments is left untouched.
        if isinstance(attachment_manager, AsyncIOBaseAttachmentManager):
            self.attachment_manager: AsyncIOBaseAttachmentManager | None = attachment_manager
        else:
            self.attachment_manager = get_asyncio_attachment_manager(
                attachment_manager, None, config
            )
        if self.attachment_manager is not None and asyncio_supports_attachments(
            self.notification_backend
        ):
            self.notification_backend.inject_attachment_manager(self.attachment_manager)

        # Resolve the git commit SHA provider (instance, dotted path, or the
        # NOTIFICATION_GIT_COMMIT_SHA_PROVIDER setting). None means the feature is off: no
        # SHA is ever resolved or written -- see _resolve_and_persist_git_commit_sha.
        if isinstance(git_commit_sha_provider, AsyncIOBaseGitCommitShaProvider):
            self.git_commit_sha_provider: AsyncIOBaseGitCommitShaProvider | None = (
                git_commit_sha_provider
            )
        else:
            self.git_commit_sha_provider = get_asyncio_git_commit_sha_provider(
                git_commit_sha_provider, None, config
            )

        if notification_adapters is None or self._check_is_adapters_tuple_iterable(
            notification_adapters
        ):
            self.notification_adapters = cast(
                Iterable[AAIO],
                get_asyncio_notification_adapters(
                    notification_adapters,
                    self.notification_backend_import_str,
                    notification_backend_kwargs if notification_backend_kwargs is not None else {},
                    config,
                ),
            )
        elif self._check_is_base_notification_adapter_iterable(notification_adapters):
            self.notification_adapters = notification_adapters
        else:
            raise NotificationError("Invalid notification adapters")

        validate_unique_adapter_notification_types(self.notification_adapters)

        self.notification_adapters_import_strs = [
            (get_class_path(adapter), get_class_path(adapter.template_renderer))
            for adapter in self.notification_adapters
        ]

    def _check_is_base_notification_adapter_iterable(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[AAIO]]:
        return notification_adapters is not None and all(
            isinstance(adapter, AsyncIOBaseNotificationAdapter) for adapter in notification_adapters
        )

    def _check_is_adapters_tuple_iterable(
        self,
        notification_adapters: Iterable[AAIO]
        | Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]
        | None,
    ) -> TypeGuard[Iterable[tuple[str, str | tuple[str, dict[str, Any]]]]]:
        return notification_adapters is not None and all(
            (isinstance(adapter, tuple) or isinstance(adapter, list))
            and len(adapter) == 2
            and (
                isinstance(adapter[0], str)
                or (
                    isinstance(adapter[0], tuple)
                    and isinstance(adapter[0][0], str)
                    and isinstance(adapter[0][1], dict)
                )
            )
            and (
                isinstance(adapter[1], str)
                or (
                    isinstance(adapter[1], tuple)
                    and isinstance(adapter[1][0], str)
                    and isinstance(adapter[1][1], dict)
                )
            )
            for adapter in notification_adapters
        )

    def _validate_attachments(
        self, attachments: list[AnyNotificationAttachment]
    ) -> list[AnyNotificationAttachment]:
        """Validate attachments and return the validated list."""
        return validate_attachments(attachments)

    async def register_queue_service(
        self, queue_service: AsyncIOBaseNotificationQueueService
    ) -> None:
        """
        Inject the queue service after construction.

        Useful when the queue service cannot exist yet at construction time -- a broker
        connection built during application startup, for example.

        Parameters:
            queue_service: AsyncIOBaseNotificationQueueService - the queue service to use for
                background sends from now on
        """
        self.notification_queue_service = queue_service

    def get_primary_backend_identifier(self) -> str:
        """Return the primary backend's identifier."""
        return self._primary_backend_identifier

    def get_all_backend_identifiers(self) -> list[str]:
        """Return every registered backend's identifier, primary first, in the order the
        backends were configured."""
        return list(self._backends.keys())

    def get_additional_backend_identifiers(self) -> list[str]:
        """Return every registered backend's identifier except the primary's, in the order
        the additional backends were configured."""
        return [
            identifier
            for identifier in self._backends
            if identifier != self._primary_backend_identifier
        ]

    def has_backend(self, backend_identifier: str) -> bool:
        """Whether `backend_identifier` names a registered backend (primary or additional)."""
        return backend_identifier in self._backends

    def _get_backend(self, backend_identifier: str | None = None) -> BAIO:
        """Resolve a backend by identifier for read routing.

        `None` resolves to the primary backend, preserving every existing call site's
        behaviour. An identifier that names no registered backend raises
        `BackendNotFoundError` rather than silently falling back to the primary.
        """
        if backend_identifier is None:
            return self._backends[self._primary_backend_identifier]
        if backend_identifier not in self._backends:
            raise BackendNotFoundError(
                f"No backend registered with identifier '{backend_identifier}'"
            )
        return self._backends[backend_identifier]

    async def _execute_multi_backend_write(
        self,
        primary_write: Callable[[BAIO], Awaitable[_WriteResultT]],
        additional_write: Callable[
            [BAIO, "Notification | OneOffNotification | None"], Awaitable[Any]
        ],
        replication_notification_id: int | str | uuid.UUID | None = None,
    ) -> _WriteResultT:
        """Run a write on the primary, then fan it out to every additional backend inline.

        The primary write runs first and its result -- and any exception -- is the caller's:
        the primary is the source of truth, so its failure is the user's failure and
        propagates unchanged. Only after it succeeds, and only when additional backends are
        configured, is the write replicated. Each replica is handled in registry order and any
        replica failure is logged and swallowed, so a rejecting replica never fails the user's
        operation -- it is reconciled by a later write. A single-backend service short-circuits
        and never enters the replication loop, staying byte-for-byte identical to a
        single-backend 2.0 deployment.

        ``primary_write`` performs the mutation on the backend it is handed. ``additional_write``
        applies the same mutation to a replica when snapshot application is unavailable; it
        receives the primary's post-write snapshot (or ``None`` when there is no single record
        to snapshot, e.g. bulk read-marking or a cancel that deleted the row).
        ``replication_notification_id`` names the record to snapshot when the primary write does
        not itself return one.
        """
        result = await primary_write(self.notification_backend)

        additional_backend_identifiers = self.get_additional_backend_identifiers()
        if not additional_backend_identifiers:
            return result

        snapshot = await self._read_replication_snapshot(replication_notification_id, result)
        for backend_identifier in additional_backend_identifiers:
            replica = self._backends[backend_identifier]
            try:
                await self._replicate_write_to_backend(replica, snapshot, additional_write)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to replicate a write to backend %s (notification %s); the primary "
                    "write succeeded and the replica will be reconciled by a later write",
                    backend_identifier,
                    getattr(snapshot, "id", replication_notification_id),
                )
        return result

    async def _read_replication_snapshot(
        self,
        replication_notification_id: int | str | uuid.UUID | None,
        result: Any,
    ) -> "Notification | OneOffNotification | None":
        """Re-read the primary's authoritative record to replicate, or ``None`` if there is none.

        Prefers ``replication_notification_id``; otherwise uses the id of the primary write's
        result when that result is itself a notification (creates and updates). Returns ``None``
        when no id resolves (e.g. bulk read-marking) or the record no longer exists on the
        primary (e.g. a cancel that deleted it) -- the caller then relies on ``additional_write``
        instead of snapshot application.

        Runs after the primary write has already committed, so it must never fail the caller:
        any exception raised by the re-read (not just ``NotificationError``, e.g. a transient
        connection error from a real backend) is logged and swallowed, degrading this replica
        pass to the ``additional_write`` fallback rather than failing an already-successful
        primary write.
        """
        snapshot_id = replication_notification_id
        if snapshot_id is None and isinstance(result, (Notification, OneOffNotification)):
            snapshot_id = result.id
        if snapshot_id is None:
            return None
        try:
            return await self.notification_backend.get_notification(snapshot_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to re-read notification %s to build a replication snapshot; the "
                "primary write already succeeded and replication will be reconciled later",
                snapshot_id,
                exc_info=True,
            )
            return None

    async def _replicate_write_to_backend(
        self,
        replica: BAIO,
        snapshot: "Notification | OneOffNotification | None",
        additional_write: Callable[
            [BAIO, "Notification | OneOffNotification | None"], Awaitable[Any]
        ],
    ) -> None:
        """Apply one write to one replica, preferring snapshot application then falling back.

        When a snapshot is available the replica's ``apply_replication_snapshot_if_newer`` is
        tried first: a backend that implements it upserts the whole record -- creating the row
        with the primary's id or refreshing it newer-wins -- in one call. When it declines
        (``applied=False``, the concrete default) the ``additional_write`` fallback mirrors the
        mutation with the backend's own primitives. A duplicate/conflict failure -- the replica
        already holds a created row, or lacks a row being updated -- flips once to converging the
        replica to the snapshot before the error propagates to the caller's logging.
        """
        try:
            if (
                snapshot is not None
                and (await replica.apply_replication_snapshot_if_newer(snapshot)).applied
            ):
                return
            await additional_write(replica, snapshot)
        except Exception as exc:  # noqa: BLE001
            if snapshot is None or not _is_likely_duplicate_replication_conflict(exc):
                raise
            await self._converge_replica_to_snapshot(replica, snapshot)

    async def _replicate_snapshot_fallback(
        self, replica: BAIO, snapshot: "Notification | OneOffNotification | None"
    ) -> None:
        """Default ``additional_write``: converge a replica to the primary's snapshot.

        Used for every single-record write (create, update, mark, store). It only converges a
        row the replica already holds; it cannot create a row with the primary's id, because no
        base primitive assigns a chosen id -- that is exactly what
        ``apply_replication_snapshot_if_newer`` exists for. A backend that implements snapshot
        application never reaches this path.
        """
        if snapshot is None:
            return
        await self._converge_replica_to_snapshot(replica, snapshot)

    async def _converge_replica_to_snapshot(
        self, replica: BAIO, snapshot: "Notification | OneOffNotification"
    ) -> None:
        """Bring an existing replica row into line with the primary's snapshot via primitives.

        Serves as both the read-then-write fallback for a backend that declines snapshot
        application and the flip target when a replica reports a duplicate/conflict. Requires the
        row to already exist on the replica; an absent row is logged and skipped, since inline
        replication cannot create a row with the primary's id without snapshot-apply support.
        """
        try:
            await replica.get_notification(snapshot.id)
        except NotificationNotFoundError:
            logger.warning(
                "Replica lacks notification %s and does not implement "
                "apply_replication_snapshot_if_newer; cannot create it with its primary id "
                "inline -- it will be reconciled later",
                snapshot.id,
            )
            return
        update_data: UpdateNotificationKwargs = {
            "title": snapshot.title,
            "body_template": snapshot.body_template,
            "context_name": snapshot.context_name,
            "context_kwargs": snapshot.context_kwargs,
            "send_after": snapshot.send_after,
            "subject_template": snapshot.subject_template,
            "preheader_template": snapshot.preheader_template,
            "adapter_extra_parameters": snapshot.adapter_extra_parameters,
        }
        await replica.persist_notification_update(
            notification_id=snapshot.id, update_data=update_data
        )
        if snapshot.context_used is not None:
            await replica.store_context_used(
                snapshot.id, snapshot.context_used, snapshot.adapter_used or ""
            )
        if snapshot.git_commit_sha is not None:
            await replica.store_git_commit_sha(snapshot.id, snapshot.git_commit_sha)
        if snapshot.status == NotificationStatus.SENT.value:
            await replica.mark_pending_as_sent(snapshot.id)
        elif snapshot.status == NotificationStatus.FAILED.value:
            await replica.mark_pending_as_failed(snapshot.id)
        elif snapshot.status == NotificationStatus.READ.value:
            await replica.mark_sent_as_read(snapshot.id)

    async def _replicate_store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: NotificationContextDict,
        adapter_import_str: str,
        lock: asyncio.Lock | None = None,
    ) -> None:
        """Persist ``context_used`` on the primary and fan it out to the replicas.

        Extracted from ``send`` / ``delayed_send`` into its own method so the
        ``_execute_multi_backend_write`` closure captures these method parameters rather than
        the send loop's ``adapter`` and ``context`` variables -- a loop-captured closure is a
        late-binding hazard the linters (rightly) reject.
        """
        await self._execute_multi_backend_write(
            lambda backend: backend.store_context_used(
                notification_id, context, adapter_import_str, lock
            ),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )

    async def _resolve_and_persist_git_commit_sha(
        self,
        notification: Notification | OneOffNotification,
        lock: asyncio.Lock | None = None,
    ) -> None:
        """Resolve the current git commit SHA and store it if it differs from what is stored.

        Called at the top of both send() and delayed_send() so foreground and background
        deliveries record the revision that actually handled this execution. A no-op when no
        provider is configured. A provider that raises is caught and logged, then treated
        the same as a None return -- audit metadata is never worth failing a delivery over.
        A non-null but malformed SHA is a configuration error and propagates
        InvalidGitCommitShaError rather than being swallowed.
        """
        if self.git_commit_sha_provider is None:
            return

        try:
            resolved_sha = await self.git_commit_sha_provider.get_current_git_commit_sha()
        except Exception:  # noqa: BLE001
            logger.exception(
                "Git commit SHA provider raised while resolving the SHA for notification "
                "%s; treating it as unknown for this execution",
                notification.id,
            )
            return

        if resolved_sha is None:
            return

        normalized_sha = normalize_git_commit_sha(resolved_sha)
        if normalized_sha == notification.git_commit_sha:
            return

        await self._execute_multi_backend_write(
            lambda backend: backend.store_git_commit_sha(notification.id, normalized_sha, lock),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification.id,
        )
        notification.git_commit_sha = normalized_sha

    async def send(
        self,
        notification: Notification | OneOffNotification,
        lock: asyncio.Lock | None = None,
        context: NotificationContextDict | None = None,
    ) -> None:
        """
        Send a notification using the appropriate adapter.

        Adapters that subclass AsyncIOBackgroundNotificationAdapter are not delivered here.
        Their notification id is handed to the configured queue service and a worker
        delivers it later through delayed_send, so no context is generated and the
        notification's status is left untouched on that path.

        If a git commit SHA provider is configured, this always resolves and (when it
        differs from what is stored) persists the current git commit SHA first, regardless
        of raise_on_failed_send. A malformed, non-null SHA raises InvalidGitCommitShaError.

        With raise_on_failed_send=False (the default) every failure below is logged and the
        remaining adapters still run. With raise_on_failed_send=True this method may raise:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationQueueServiceMissingError if a background adapter has no queue
              service to enqueue through;
            * NotificationSendError if the adapter fails to send the notification, or the
              queue service fails to accept it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification: Notification | OneOffNotification - the notification to be sent
            lock: asyncio.Lock | None - serializes concurrent backend writes when this
                notification is sent as part of a send_pending_notifications() batch
            context: NotificationContextDict | None - a pre-computed context to send with,
                bypassing context generation entirely. Used by ``resend_notification`` to reuse
                a stored ``context_used`` verbatim; regular callers leave this ``None`` so
                context is generated as usual.
        """
        await self._resolve_and_persist_git_commit_sha(notification, lock)

        # Context is generated lazily below, and only for adapters that deliver in this
        # process: the enqueue branch must not pay for a context the worker will generate
        # again anyway. A caller such as resend_notification may pass a context in, in which
        # case it is used as-is and no generation happens.

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if isinstance(adapter, AsyncIOBackgroundNotificationAdapter):
                if self.notification_queue_service is None:
                    logger.error(
                        "Cannot send notification %s in the background: adapter %s requires a "
                        "queue service and none is configured",
                        notification.id,
                        adapter.adapter_import_str,
                    )
                    if self.raise_on_failed_send:
                        raise NotificationQueueServiceMissingError(
                            "No notification queue service is configured"
                        )
                    continue
                try:
                    await self.notification_queue_service.enqueue_notification(notification.id)
                except NotificationError as e:
                    # Queue services are contractually required to wrap broker failures in a
                    # NotificationError subclass. Anything they leave unwrapped escapes here,
                    # as their base class documents.
                    logger.exception("Failed to enqueue notification %s", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationSendError("Failed to enqueue notification") from e
                continue

            if not isinstance(adapter, AsyncIOBaseNotificationAdapter):
                continue

            if context is None:
                try:
                    context = await self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification.id
                    )
                    try:
                        await self._execute_multi_backend_write(
                            lambda backend: backend.mark_pending_as_failed(notification.id, lock),
                            self._replicate_snapshot_fallback,
                            replication_notification_id=notification.id,
                        )
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification.id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    # The context belongs to the notification, not to one adapter, so no
                    # other adapter could send it either.
                    return

            try:
                await adapter.send(
                    notification=notification,
                    context=context,
                )
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification.id)
                try:
                    await self._execute_multi_backend_write(
                        lambda backend: backend.mark_pending_as_failed(notification.id, lock),
                        self._replicate_snapshot_fallback,
                        replication_notification_id=notification.id,
                    )
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification.id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                await self._execute_multi_backend_write(
                    lambda backend: backend.mark_pending_as_sent(notification.id, lock),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=notification.id,
                )
                await self._replicate_store_context_used(
                    notification.id, context, adapter.adapter_import_str, lock
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification.id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

    async def create_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> Notification:
        """
        Create a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to send the notification to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[AnyNotificationAttachment] | None - attachments to include
            tenant: str | None - the tenant this notification belongs to. Cannot be changed
                after creation -- see ``update_notification``.
        """
        validated_attachments = self._validate_attachments(attachments or [])

        persist_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "notification_type": notification_type,
            "title": title,
            "body_template": body_template,
            "context_name": context_name,
            "context_kwargs": context_kwargs,
            "send_after": send_after,
            "subject_template": subject_template,
            "preheader_template": preheader_template,
            "adapter_extra_parameters": adapter_extra_parameters,
        }
        # Only pass ``attachments`` when there is something to attach: backends that do
        # not accept the keyword (e.g. attachment-unaware backends) then keep working.
        if validated_attachments:
            persist_kwargs["attachments"] = validated_attachments

        if tenant is not None:
            persist_kwargs["tenant"] = tenant

        notification = await self._execute_multi_backend_write(
            lambda backend: backend.persist_notification(**persist_kwargs),
            self._replicate_snapshot_fallback,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def create_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: NotificationContextDict,
        send_after: datetime.datetime | None = None,
        subject_template: str = "",
        preheader_template: str = "",
        adapter_extra_parameters: dict | None = None,
        attachments: list[AnyNotificationAttachment] | None = None,
        tenant: str | None = None,
    ) -> OneOffNotification:
        """
        Create a one-off notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * InvalidOneOffNotificationRecipientError if email_or_phone is invalid;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            email_or_phone: str - the email or phone that the one-off notification will be
                sent to
            notification_type: str - the type of notification to send
            title: str - the title of the notification
            body_template: str - the string that represents the body template
            context_name: str - the name of the context function to generate the context
            context_kwargs: NotificationContextDict - the context kwargs to generate the context
            send_after: datetime.datetime | None - the date and time to send the notification
            subject_template: str - the  string that represents the subject template
            preheader_template: str - the string that represents the preheader template
            attachments: list[AnyNotificationAttachment] | None - attachments to include
            tenant: str | None - the tenant this notification belongs to. Cannot be changed
                after creation -- see ``update_notification``.
        """
        validate_email_or_phone(email_or_phone)
        validated_attachments = self._validate_attachments(attachments or [])

        persist_kwargs: dict[str, Any] = {
            "email_or_phone": email_or_phone,
            "first_name": first_name,
            "last_name": last_name,
            "notification_type": notification_type,
            "title": title,
            "body_template": body_template,
            "context_name": context_name,
            "context_kwargs": context_kwargs,
            "send_after": send_after,
            "subject_template": subject_template,
            "preheader_template": preheader_template,
            "adapter_extra_parameters": adapter_extra_parameters,
        }
        if validated_attachments:
            persist_kwargs["attachments"] = validated_attachments

        if tenant is not None:
            persist_kwargs["tenant"] = tenant

        notification = await self._execute_multi_backend_write(
            lambda backend: backend.persist_one_off_notification(**persist_kwargs),
            self._replicate_snapshot_fallback,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def update_notification(
        self,
        notification_id: int | str | uuid.UUID,
        **kwargs: Unpack[UpdateNotificationKwargs],
    ) -> Notification | OneOffNotification:
        """
        Update a notification and send it if it is due to be sent immediately.

        This method may raise the following exceptions:
            * TenantReassignmentError if ``tenant`` is present in kwargs -- a
              notification's tenant cannot be reassigned after creation.
            * GitCommitShaReassignmentError if ``git_commit_sha`` is present in kwargs --
              it is system-managed and only ever written by AsyncIONotificationService at
              send time.
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to update
            **kwargs: UpdateNotificationKwargs - the fields to update
        """
        if "tenant" in kwargs:
            raise TenantReassignmentError("A notification's tenant cannot be reassigned")
        if "git_commit_sha" in kwargs:
            raise GitCommitShaReassignmentError(
                "A notification's git_commit_sha is system-managed and cannot be set directly"
            )
        notification = await self._execute_multi_backend_write(
            lambda backend: backend.persist_notification_update(
                notification_id=notification_id,
                update_data=kwargs,
            ),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )
        if notification.send_after is None or notification.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(notification)
        return notification

    async def get_all_future_notifications(
        self, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return await self._get_backend(backend_identifier).get_all_future_notifications()

    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications from the user
        """
        return await self._get_backend(backend_identifier).get_all_future_notifications_from_user(
            user_id
        )

    async def get_future_notifications_from_user(
        self,
        user_id: int | str | uuid.UUID,
        page: int,
        page_size: int,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of the future notifications from the user
        """
        return await self._get_backend(backend_identifier).get_future_notifications_from_user(
            user_id, page, page_size
        )

    async def get_future_notifications(
        self, page: int, page_size: int, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get future notifications from the backend.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the future notifications
        """
        return await self._get_backend(backend_identifier).get_future_notifications(page, page_size)

    async def get_notification_context(
        self, notification: Notification | OneOffNotification
    ) -> NotificationContextDict:
        """
        Generate the context for a notification. It uses the context_name and context_kwargs from the notification.
        Contexts are registered using the @register_context decorator.

        This method may raise the following exceptions:
            * NotificationContextGenerationError if the context generation fails.

        Parameters:
            notification: Notification | OneOffNotification - the notification to generate the context for
        """
        context_function = Contexts().get_function(notification.context_name)
        if context_function is None:
            raise NotificationContextGenerationError("Context function not found")
        try:
            if self._is_asyncio_context_function(context_function):
                return await context_function(*[], **notification.context_kwargs)
            elif self._is_sync_context_function(context_function):
                return context_function(*[], **notification.context_kwargs)
            raise NotificationContextGenerationError("Invalid context function")
        except Exception as e:  # noqa: BLE001
            raise NotificationContextGenerationError("Failed getting notification context") from e

    def _is_asyncio_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], Coroutine[Any, Any, NotificationContextDict]]]:
        return is_asyncio_context_function(context_function)

    def _is_sync_context_function(
        self,
        context_function: Callable[[Any], NotificationContextDict]
        | Callable[[Any], Coroutine[Any, Any, NotificationContextDict]],
    ) -> TypeGuard[Callable[[Any], NotificationContextDict]]:
        return is_sync_context_function(context_function)

    async def _send_notification_with_error_logging(
        self, notification: "Notification | OneOffNotification", lock: asyncio.Lock | None = None
    ) -> bool:
        """
        Send a notification, logging success or failure, and report whether it counts as sent.

        Returns:
            bool - True if the notification counts as sent, False if it counts as failed.
        """
        try:
            await self.send(notification, lock)
        except NotificationSendError:
            logger.exception("Failed to send notification %s", notification.id)
            return False
        except NotificationMarkFailedError:
            logger.exception("Failed to send notification %s", notification.id)
            logger.exception("Failed to mark notification %s as failed", notification.id)
            return False
        except NotificationMarkSentError:
            logger.info("Notification %s sent", notification.id)
            logger.exception("Failed to mark notification %s as sent", notification.id)
            return True
        else:
            logger.info("Notification %s sent", notification.id)
            return True

    async def send_pending_notifications(self) -> None:
        """
        Send all pending notifications in the backend.

        This method doesn't raise any exceptions, but it provides specific logs for each
        notification (success or failure) and a summary at the end with the number of notifications
        sent and failed.
        """

        pending_notifications = await self.notification_backend.get_all_pending_notifications()
        lock = asyncio.Lock()
        results = await asyncio.gather(
            *[
                self._send_notification_with_error_logging(notification, lock)
                for notification in pending_notifications
            ]
        )
        notifications_sent = sum(1 for result in results if result)
        notifications_failed = len(results) - notifications_sent

        logger.info("Sent %s notifications", notifications_sent)
        logger.info("Failed to send %s notifications", notifications_failed)

    async def get_pending_notifications(
        self, page: int, page_size: int, backend_identifier: str | None = None
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Get pending notifications from the backend.

        Parameters:
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the pending notifications
        """
        return await self._get_backend(backend_identifier).get_pending_notifications(
            page, page_size
        )

    async def get_notification(
        self, notification_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> Notification | OneOffNotification:
        """
        Get a notification from the backend.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to get
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Notification | OneOffNotification - the notification
        """
        return await self._get_backend(backend_identifier).get_notification(notification_id)

    async def mark_read(
        self, notification_id: int | str | uuid.UUID
    ) -> Notification | OneOffNotification:
        """
        Mark a notification as read.

        This method may raise the following exceptions:
            * NotificationUpdateError if the notification fails to be marked as read.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to mark as read

        Returns:
            Notification | OneOffNotification - the updated notification
        """
        return await self._execute_multi_backend_write(
            lambda backend: backend.mark_sent_as_read(notification_id),
            self._replicate_snapshot_fallback,
            replication_notification_id=notification_id,
        )

    async def mark_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
    ) -> Iterable[Notification]:
        """
        Mark multiple notifications as read at once.

        This is idempotent: ids that are already read, missing, not owned by
        ``user_id`` (when provided), or in a non-SENT state are simply skipped --
        no error is raised. When ``user_id`` is provided the update is scoped to
        that user; passing it is strongly recommended for endpoint use.

        Parameters:
            notification_ids: Iterable[int | str | uuid.UUID] - the notifications to mark as read
            user_id: int | str | uuid.UUID | None - optional owner scope

        Returns:
            Iterable[Notification] - the requested notifications that are read after the operation
        """
        # Materialize once: the id iterable may be a single-use generator, and both the primary
        # write and every replica mirror need to consume it.
        ids = list(notification_ids)
        return await self._execute_multi_backend_write(
            lambda backend: backend.mark_sent_as_read_bulk(ids, user_id=user_id),
            lambda backend, snapshot: backend.mark_sent_as_read_bulk(ids, user_id=user_id),
        )

    async def get_in_app_unread(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification]:
        """
        Get unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification] - the unread in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self._get_backend(backend_identifier).filter_in_app_unread_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    async def get_in_app_notifications(
        self,
        user_id: int | str | uuid.UUID,
        page: int = 1,
        page_size: int = 10,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification]:
        """
        Get all in-app notifications (read + unread) for a user, paginated.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.

        Parameters:
            user_id: int | str | uuid.UUID - the user ID to get the notifications for
            page: int - the page number to get
            page_size: int - the number of notifications per page
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification] - the in-app notifications
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self._get_backend(backend_identifier).filter_in_app_notifications(
            user_id=user_id, page=page, page_size=page_size
        )

    async def get_in_app_notifications_count(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> int:
        """
        Get the total count of in-app notifications (read + unread) for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self._get_backend(backend_identifier).count_in_app_notifications(user_id)

    async def get_in_app_unread_count(
        self, user_id: int | str | uuid.UUID, backend_identifier: str | None = None
    ) -> int:
        """
        Get the total count of unread in-app notifications for a user.

        This method may raise the following exceptions:
            * NotificationError if no in-app notification adapter is found.
        """
        if not any(
            a.notification_type == NotificationTypes.IN_APP for a in self.notification_adapters
        ):
            raise NotificationError("No in-app notification adapter found")
        return await self._get_backend(backend_identifier).count_in_app_unread_notifications(
            user_id
        )

    async def filter_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        page: int,
        page_size: int,
        order_by: NotificationOrderBy | None = None,
        backend_identifier: str | None = None,
    ) -> Iterable[Notification | OneOffNotification]:
        """
        Query notifications with a composable filter, ordering and pagination.

        The filter supports ``and`` / ``or`` / ``not`` groups, scalar-or-list membership, string
        lookups and inclusive date ranges. An empty filter (``{}``) matches every notification.
        Both ``Notification`` and ``OneOffNotification`` are returned. See
        ``AsyncIOBaseNotificationBackend.filter_notifications`` for the full semantics.

        Parameters:
            filter: NotificationFilter - the composable filter (``{}`` matches everything)
            page: int - the 1-indexed page number to get
            page_size: int - the number of notifications per page
            order_by: NotificationOrderBy | None - the primary sort field and direction
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            Iterable[Notification | OneOffNotification] - the selected page of matches
        """
        return await self._get_backend(backend_identifier).filter_notifications(
            filter, page=page, page_size=page_size, order_by=order_by
        )

    async def count_notifications(
        self,
        filter: NotificationFilter,  # noqa: A002
        backend_identifier: str | None = None,
    ) -> int:
        """
        Count notifications matching ``filter``, ignoring pagination.

        This is the total a dashboard needs to render pagination; ``filter_notifications``
        returns an ``Iterable`` that cannot be ``len()``-ed.

        Parameters:
            filter: NotificationFilter - the composable filter (``{}`` counts everything)
            backend_identifier: str | None - which registered backend to read from; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            int - the number of matching notifications
        """
        return await self._get_backend(backend_identifier).count_notifications(filter)

    async def get_backend_supported_filter_capabilities(
        self, backend_identifier: str | None = None
    ) -> dict[str, bool]:
        """
        Report which filter capabilities the configured backend supports.

        The backend declares only what it *cannot* do; this merges that report OVER an
        all-``True`` default, so every capability the backend does not mention comes back
        ``True``. Keys are camelCase dotted (``'fields.notificationType'``, ``'orderBy.sentAt'``),
        kept byte-identical to the TypeScript sibling so one dashboard consumes either.

        Parameters:
            backend_identifier: str | None - which registered backend to report on; the
                primary backend when omitted. Raises BackendNotFoundError if unknown.

        Returns:
            dict[str, bool] - the merged capability report
        """
        return {
            **DEFAULT_BACKEND_FILTER_CAPABILITIES,
            **await self._get_backend(backend_identifier).get_filter_capabilities(),
        }

    async def resend_notification(
        self,
        notification_id: int | str | uuid.UUID,
        use_stored_context_if_available: bool = False,
    ) -> Notification:
        """
        Resend a notification by cloning it into a brand-new PENDING row and sending that
        clone immediately. This is the operation a dashboard "retry" action drives.

        The source notification is re-read from the backend and left completely untouched --
        its id, status and timestamps never change. The clone is a new row with its own id,
        carrying over the source's user, template, context and attachment configuration.

        This method may raise the following exceptions:
            * NotificationResendError if the notification is a one-off, or is still scheduled
              in the future (``send_after`` set and not yet due).
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if the adapter fails to send the notification.
            * NotificationMarkFailedError if the notification fails to be marked as failed.
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Parameters:
            notification_id: int | str | uuid.UUID - the notification to resend
            use_stored_context_if_available: bool - when True and the source notification has
                a stored ``context_used``, the clone reuses it verbatim instead of regenerating
                context at send time. Defaults to False.

        Returns:
            Notification - the newly created clone
        """
        source = await self.notification_backend.get_notification(notification_id)
        if isinstance(source, OneOffNotification):
            raise NotificationResendError("One-off notifications cannot be resent")
        if source.send_after is not None and source.send_after > datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            raise NotificationResendError(
                "Cannot resend a notification that is still scheduled in the future"
            )

        extra_persist_kwargs: dict[str, Any] = {}
        if source.tenant is not None:
            extra_persist_kwargs["tenant"] = source.tenant

        clone = await self._execute_multi_backend_write(
            lambda backend: backend.persist_notification(
                user_id=source.user_id,
                notification_type=source.notification_type,
                title=source.title,
                body_template=source.body_template,
                context_name=source.context_name,
                context_kwargs=source.context_kwargs,
                send_after=None,
                subject_template=source.subject_template,
                preheader_template=source.preheader_template,
                adapter_extra_parameters=source.adapter_extra_parameters,
                **extra_persist_kwargs,
            ),
            self._replicate_snapshot_fallback,
        )

        if source.attachments:
            clone = cast(
                Notification,
                await self._execute_multi_backend_write(
                    lambda backend: backend.persist_notification_update(
                        notification_id=clone.id,
                        update_data={"attachments": list(source.attachments)},
                    ),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=clone.id,
                ),
            )

        reused_context: NotificationContextDict | None = None
        if use_stored_context_if_available and source.context_used is not None:
            reused_context = cast(NotificationContextDict, source.context_used)

        if clone.send_after is None or clone.send_after <= datetime.datetime.now(
            tz=datetime.timezone.utc
        ):
            await self.send(clone, context=reused_context)
        return clone

    async def cancel_notification(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Cancel a notification.

        Parameters:
            notification_id: int | str | uuid.UUID - the ID of the notification to cancel
        """

        async def _cancel_on_replica(
            backend: BAIO, snapshot: "Notification | OneOffNotification | None"
        ) -> None:
            # A cancel deletes the row, so there is no snapshot to apply -- mirror the delete on
            # the replica. A replica that never held the row is already in the desired (absent)
            # state, so a not-found is success, not a failure.
            try:
                await backend.cancel_notification(notification_id)
            except NotificationNotFoundError:
                return

        return await self._execute_multi_backend_write(
            lambda backend: backend.cancel_notification(notification_id),
            _cancel_on_replica,
            replication_notification_id=notification_id,
        )

    async def delayed_send(self, notification_id: int | str | uuid.UUID) -> None:
        """
        Deliver a notification from a background worker, given only its id.

        This is the worker half of the background path: `send()` hands the notification id to
        the queue service, and the worker calls this. The notification is reloaded from the
        backend and its context is generated here, at delivery time, so a scheduled
        notification renders against current data exactly as a foreground send does.

        Delivery is at-least-once, so this may be called twice for the same id. A notification
        that has already been delivered (or cancelled) is skipped rather than sent again.

        With raise_on_failed_send=False (the default) failures are logged rather than raised.
        With raise_on_failed_send=True this method may raise:
            * NotificationNotFoundError if the id does not resolve to a notification;
            * NotificationContextGenerationError if the context generation fails;
            * NotificationSendError if no background adapter handles the notification's type,
              or the adapter fails to send it;
            * NotificationMarkFailedError if the notification fails to be marked as failed;
            * NotificationMarkSentError if the notification fails to be marked as sent.

        Note that NotificationNotFoundError propagates regardless of raise_on_failed_send:
        an id that resolves to nothing is a wiring or retention problem, not a failed send.

        If a git commit SHA provider is configured, this always resolves and (when it
        differs from what is stored) persists the current git commit SHA before delivering,
        so the worker records the revision that actually sent the notification. A malformed,
        non-null SHA raises InvalidGitCommitShaError.

        Parameters:
            notification_id: int | str | uuid.UUID - the id of the notification to deliver
        """
        notification = await self.notification_backend.get_notification(notification_id)

        if notification.status in ALREADY_DELIVERED_NOTIFICATION_STATUSES:
            logger.info(
                "Skipping background send of notification %s: its status is already %s",
                notification_id,
                notification.status,
            )
            return

        # Resolved here, after the already-delivered check: the SHA records the revision
        # that sends the notification, so a skipped redelivery of an already-sent row must
        # not overwrite it with a later, unrelated revision.
        await self._resolve_and_persist_git_commit_sha(notification)

        context: NotificationContextDict | None = None
        background_adapter_found = False

        for adapter in self.notification_adapters:
            if adapter.notification_type.value != notification.notification_type:
                continue

            if not isinstance(adapter, AsyncIOBackgroundNotificationAdapter):
                # A foreground adapter has nothing to do in a worker, but the ones after it
                # in the list might, so keep going.
                continue

            background_adapter_found = True

            if context is None:
                try:
                    context = await self.get_notification_context(notification)
                except NotificationContextGenerationError as context_error:
                    logger.exception(
                        "Failed to generate context for notification %s", notification_id
                    )
                    try:
                        await self._execute_multi_backend_write(
                            lambda backend: backend.mark_pending_as_failed(notification_id),
                            self._replicate_snapshot_fallback,
                            replication_notification_id=notification_id,
                        )
                    except NotificationUpdateError as e:
                        logger.exception(
                            "Failed to mark notification %s as failed", notification_id
                        )
                        if self.raise_on_failed_send:
                            raise NotificationMarkFailedError(
                                "Failed to mark notification as failed"
                            ) from e
                        return
                    if self.raise_on_failed_send:
                        raise context_error
                    return

            try:
                await adapter.send(notification=notification, context=context)
            except Exception as adapter_error:  # noqa: BLE001
                send_error = NotificationSendError("Failed to send notification")
                logger.exception("Failed to send notification %s", notification_id)
                try:
                    await self._execute_multi_backend_write(
                        lambda backend: backend.mark_pending_as_failed(notification_id),
                        self._replicate_snapshot_fallback,
                        replication_notification_id=notification_id,
                    )
                except NotificationUpdateError:
                    logger.exception("Failed to mark notification %s as failed", notification_id)
                    if self.raise_on_failed_send:
                        raise NotificationMarkFailedError(
                            "Failed to mark notification as failed"
                        ) from send_error
                    continue
                if self.raise_on_failed_send:
                    raise send_error from adapter_error
                continue

            try:
                await self._execute_multi_backend_write(
                    lambda backend: backend.mark_pending_as_sent(notification_id),
                    self._replicate_snapshot_fallback,
                    replication_notification_id=notification_id,
                )
                await self._replicate_store_context_used(
                    notification_id, context, adapter.adapter_import_str
                )
            except NotificationUpdateError as e:
                logger.exception("Failed to mark notification %s as sent", notification_id)
                if self.raise_on_failed_send:
                    raise NotificationMarkSentError("Failed to mark notification as sent") from e

        if not background_adapter_found:
            logger.error(
                "No background notification adapter is configured for notification %s of type %s",
                notification_id,
                notification.notification_type,
            )
            if self.raise_on_failed_send:
                raise NotificationSendError(
                    "No background notification adapter found for this notification"
                )
