import asyncio
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable

from vintasend.constants import REPINNABLE_NOTIFICATION_STATUSES, UNSET, UnsetType
from vintasend.exceptions import NotificationRepinError, UnsupportedNotificationActionError
from vintasend.services.dataclasses import ApplyResult
from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
    from vintasend.services.dataclasses import (
        AnyNotificationAttachment,
        AttachmentFileRecord,
        Notification,
        NotificationVersion,
        OneOffNotification,
        StoredAttachment,
        UpdateNotificationKwargs,
    )
    from vintasend.services.notification_backends.filters import (
        NotificationFilter,
        NotificationOrderBy,
    )


class AsyncIOBaseNotificationBackend(ABC):
    """The AsyncIO storage seam: where notifications are persisted, queried and updated.

    **Writing the primary's version token (``writes.notificationVersion``).** A backend that
    reports ``writes.notificationVersion`` in ``get_action_capabilities()`` must accept a
    keyword-only ``notification_version: str | None = None`` on every write method that changes
    a single notification -- ``persist_notification``, ``persist_one_off_notification``,
    ``persist_notification_update``, ``mark_pending_as_sent``, ``mark_pending_as_failed``,
    ``mark_sent_as_read``, ``cancel_notification``, ``requeue_notification``,
    ``repin_notification``, ``store_context_used``, ``store_git_commit_sha``,
    ``store_template_version`` and ``delete_notification_attachment`` -- and a keyword-only
    ``notification_versions: Mapping[id, str] | None = None`` on ``mark_sent_as_read_bulk``,
    one token per row. A given token is the one the row must hold after the write, instead of
    one the backend issues; ``None`` (or a row missing from the mapping) means issue one as
    usual. Bulk keys are the ids as the primary returned them, so compare them as strings when
    id types may differ.

    The keyword is not in the abstract signatures, so an override written before 3.8.0 still
    type-checks; widening a signature with it is a valid override. It stays part of the contract
    on every write method listed above, and the bulk ``notification_versions`` mapping is
    accepted by it too, but the service itself uses the keyword for one thing only: stamping.

    **The stamp.** A replica holds the primary's token only once its row verifiably matches the
    primary's snapshot. When an additional backend reporting the capability does not apply the
    primary's snapshot (it declines it, or does not implement
    ``apply_replication_snapshot_if_newer``), the service replays the write there with no
    token, so every replayed write issues the replica's own; converges the row to the snapshot
    (status, content, template pin, context used, git commit SHA and, when the backend overrides
    ``store_template_version``, the template version used); re-reads the row and compares it
    with the snapshot; and only when they match makes one token-only write::

        persist_notification_update(notification_id, {}, notification_version=<primary token>)

    So a backend reporting ``writes.notificationVersion`` MUST accept an empty ``update_data``
    with a ``notification_version`` in ANY status, ``SENT`` and ``READ`` included, as a
    token-only write that changes nothing else -- not the content, not ``modified``. If the row
    does not match, the snapshot has no token, or any write raised, there is no stamp: the
    replica keeps a token of its own, and a move with a version listed from it is refused,
    which is the safe outcome. Bulk read-marking stamps row by row the same way.

    The primary never receives the keyword: the primary issues tokens. Nor can it get a replica
    a row it lacks: no write creates a row with the primary's id, which is what
    ``apply_replication_snapshot_if_newer`` is for.
    """

    def __init__(self, *args, **kwargs):
        self.backend_import_str = get_class_path(self)
        self.config = kwargs.pop("config", None)
        self.backend_kwargs = kwargs
        # A backend never reads or writes a byte itself. When the service is configured
        # with an attachment manager it injects it here; otherwise this stays None and the
        # backend simply persists notifications without attachments.
        self._attachment_manager: "AsyncIOBaseAttachmentManager | None" = None

    @abstractmethod
    async def get_all_pending_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_pending_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_all_future_notifications(
        self,
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_future_notifications(
        self, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_all_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def get_future_notifications_from_user(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification | OneOffNotification"]: ...

    @abstractmethod
    async def persist_notification(
        self,
        user_id: int | str | uuid.UUID,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
        adapter_extra_parameters: dict | None = None,
        attachments: list["AnyNotificationAttachment"] | None = None,
        tenant: str | None = None,
        # Optional to accept only in the sense that a backend predating template versioning
        # never receives it: NotificationService passes it solely when a version was pinned.
        # A backend that stores it can answer "which version was this notification recorded
        # against" without re-deriving it from a template store that has moved on since.
        requested_template_version: int | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "Notification":
        """Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def persist_one_off_notification(
        self,
        email_or_phone: str,
        first_name: str,
        last_name: str,
        notification_type: str,
        title: str,
        body_template: str,
        context_name: str,
        context_kwargs: dict[str, uuid.UUID | str | int],
        send_after: datetime.datetime | None,
        subject_template: str,
        preheader_template: str,
        adapter_extra_parameters: dict | None = None,
        attachments: list["AnyNotificationAttachment"] | None = None,
        tenant: str | None = None,
        # Optional to accept only in the sense that a backend predating template versioning
        # never receives it: NotificationService passes it solely when a version was pinned.
        # A backend that stores it can answer "which version was this notification recorded
        # against" without re-deriving it from a template store that has moved on since.
        requested_template_version: int | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "OneOffNotification":
        """Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def persist_notification_update(
        self,
        notification_id: int | str | uuid.UUID,
        update_data: "UpdateNotificationKwargs",
        lock: asyncio.Lock | None = None,
    ) -> "Notification | OneOffNotification":
        """
        Update a notification in the backend. This method should return the updated notification.
        Notifications that have already been sent should not be updated.
        If a notification has already been sent, the method should raise a NotificationUpdateError.

        A notification that did not go out (``PENDING_SEND``, ``FAILED`` or ``CANCELLED``, the
        statuses in ``vintasend.constants.REPINNABLE_NOTIFICATION_STATUSES``) must be
        updatable. ``AsyncIONotificationService.repin_notification`` writes through this method.

        The service passes ``notification_id`` and ``update_data`` positionally, because
        released implementations name the second parameter ``updated_data``. Keep the
        seam's names in a new implementation all the same.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring. Such a backend must
        then accept an empty ``update_data`` with a ``notification_version`` in any status,
        ``SENT`` and ``READ`` included, as a token-only write that changes nothing else: it is
        how the service stamps a converged replica row with the primary's token.
        """
        ...

    @abstractmethod
    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a pending notification as sent. Implementations must set ``sent_at`` to
        the current time on the affected row.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a sent notification as read. Implementations must set ``read_at`` to
        the current time on the affected row.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def mark_sent_as_read_bulk(
        self,
        notification_ids: Iterable[int | str | uuid.UUID],
        user_id: int | str | uuid.UUID | None = None,
        lock: asyncio.Lock | None = None,
    ) -> Iterable["Notification"]:
        """
        Mark multiple notifications as read at once.

        Semantics:
            * Every notification in ``notification_ids`` that is currently SENT is
              moved to READ, and ``read_at`` is set to the current time on every
              row moved this way.
            * If ``user_id`` is provided, the update is scoped to that user; rows
              owned by other users are never touched. This is the safe default for
              an endpoint and callers are strongly encouraged to always pass it.
            * Idempotent: ids that are already READ cause no error and their
              ``read_at`` is left untouched.
            * Returns the serialized notifications for the requested ids that are
              READ after the operation (newly-marked + already-read), so the caller
              sees the final state. Ids that are missing, not owned, or in a
              non-SENT-non-READ state are omitted from the result.

        Unlike ``mark_sent_as_read``, this method NEVER raises when zero rows are
        updated -- it is idempotent by construction.

        Takes a keyword-only ``notification_versions: Mapping[id, str] | None = None`` when the
        backend reports ``writes.notificationVersion``; see the class docstring. The contract
        accepts the mapping, but the service does not pass it: it stamps row by row.
        """
        ...

    @abstractmethod
    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
        """Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def requeue_notification(
        self,
        notification_id: int | str | uuid.UUID,
        send_after: datetime.datetime | UnsetType | None = UNSET,
        lock: asyncio.Lock | None = None,
    ) -> "Notification | OneOffNotification":
        """Put a notification that did not go out back into ``PENDING_SEND``, so it is sent again.

        Works for regular and one-off notifications alike. An implementation must:

        * only move a notification whose status is one of
          ``vintasend.constants.REQUEUEABLE_NOTIFICATION_STATUSES`` (``FAILED`` and
          ``CANCELLED``). Where the store allows it, check the status and write in one step, so
          two callers racing cannot both succeed, for example against a notification a worker
          sent in the meantime. A SQL backend does this with a conditional
          ``UPDATE ... WHERE id = ? AND status IN (...)`` and checks the row count;
        * raise ``NotificationNotFoundError`` when no notification has that ID, and
          ``NotificationRequeueError`` when it exists in any other status. Name the
          notification by its ID only in either message;
        * set the status to ``PENDING_SEND`` and clear ``sent_at``;
        * set ``send_after`` to the given value when one is passed, ``None`` included, and leave
          it as it is when the argument is left out (``UNSET``).

        Everything else stays as it is. The template pin, ``context_used``, ``adapter_used``,
        the git commit SHA and the attachments are history of the earlier attempt, and the next
        send overwrites what it needs to.

        Return the full notification, attachments included, the same way ``get_notification``
        does. The service sends whatever this returns.

        Two optional keyword-only arguments, added in 3.8.0, are deliberately not part of the
        abstract signature above, so an override written for 3.7 still type-checks. A backend
        that reports ``requeue.templateVersion`` or ``requeue.expectedVersion`` in
        ``get_action_capabilities()`` must accept the matching argument as keyword-only
        (widening the signature is a valid override). The service passes each one only to a
        backend that reports it, so a backend that does not support them leaves them out of
        its signature:

        * ``template_version`` (``requeue.templateVersion``): set ``requested_template_version``
          in the same write, so the notification is never left pinned but not pending, or
          pending on the old pin.
        * ``expected_version`` (``requeue.expectedVersion``): write only if the notification is
          still at this version, the token ``filter_notification_versions`` returned for it, and
          raise ``NotificationVersionChangedError`` otherwise, writing nothing. A SQL backend
          adds the version column to the ``WHERE``; when nothing matched, it reads the row once
          to say why.

        A backend that reports neither must raise if it is handed one anyway, never ignore it.

        Required since 3.7.0. A backend written against an earlier version must add it.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def get_notification(
        self, notification_id: int | str | uuid.UUID, for_update=False
    ) -> "Notification | OneOffNotification": ...

    @abstractmethod
    async def filter_all_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]: ...

    @abstractmethod
    async def filter_in_app_unread_notifications(
        self, user_id: int | str | uuid.UUID, page: int, page_size: int
    ) -> Iterable["Notification"]: ...

    @abstractmethod
    async def filter_all_in_app_notifications(
        self, user_id: int | str | uuid.UUID
    ) -> Iterable["Notification"]:
        """
        Return all in-app notifications (read + unread) for a user, unpaginated.

        "All" means ``notification_type == IN_APP`` AND ``status in (SENT, READ)``;
        internal pipeline states (PENDING_SEND, FAILED, CANCELLED) are excluded.

        Prefer the paginated ``filter_in_app_notifications`` + ``count_in_app_notifications``
        for end-user facing listings; this unpaginated variant is meant for internal
        and count use.
        """
        ...

    @abstractmethod
    async def filter_in_app_notifications(
        self, user_id: int | str | uuid.UUID, page: int = 1, page_size: int = 10
    ) -> Iterable["Notification"]:
        """
        Return a page of in-app notifications (read + unread) for a user.

        Same SENT/READ filtering as ``filter_all_in_app_notifications``.
        """
        ...

    async def count_in_app_notifications(self, user_id: int | str | uuid.UUID) -> int:
        """
        Total number of in-app notifications (read + unread) for a user.

        Concrete default derived from ``filter_all_in_app_notifications`` so existing
        backends keep working without changes. Backends SHOULD override this for
        efficiency (e.g. a database ``COUNT``).
        """
        return sum(1 for _ in await self.filter_all_in_app_notifications(user_id))

    async def count_in_app_unread_notifications(self, user_id: int | str | uuid.UUID) -> int:
        """
        Total number of unread in-app notifications for a user.

        Concrete default derived from ``filter_all_in_app_unread_notifications`` so
        existing backends keep working without changes. Backends SHOULD override this
        for efficiency (e.g. a database ``COUNT``).
        """
        return sum(1 for _ in await self.filter_all_in_app_unread_notifications(user_id))

    @abstractmethod
    async def filter_notifications(
        self,
        filter: "NotificationFilter",  # noqa: A002
        page: int,
        page_size: int,
        order_by: "NotificationOrderBy | None" = None,
    ) -> Iterable["Notification | OneOffNotification"]:
        """
        Return one page of notifications matching a composable filter.

        This is the general-purpose query a monitoring dashboard consumes. It covers BOTH
        ``Notification`` and ``OneOffNotification`` -- a dashboard wants one list -- so a caller
        that must separate them does so on the returned objects, not via the filter.

        Filter semantics:
            * An **empty filter (``{}``) matches every notification** -- it is the unrestricted
              listing. "Empty filter returns nothing" is an equally plausible but WRONG reading;
              a backend that gets this backwards silently hides every row.
            * Multiple keys inside one field filter are an implicit ``AND``. A scalar means
              equality; a list means membership. ``and`` / ``or`` / ``not`` compose and nest
              arbitrarily.
            * **Date-range bounds are inclusive on both ends** (``from`` -> ``>=``, ``to`` ->
              ``<=``). A client computing "today" from midnight to midnight double-counts
              boundary rows if an implementation makes them exclusive.
            * A positive filter on a field whose value is ``None`` does not match; consequently a
              ``None`` row IS included under negation (``not``). Nullable columns must include
              their ``None`` rows under negation.

        Ordering:
            * ``order_by`` selects a single primary sort field and direction; ``None`` is the
              backend's documented default.
            * Ordering MUST be **stable**: implementations append ``id`` as a tiebreaker in the
              SAME direction as the primary sort key. ``created`` and ``modified`` are not
              unique, and offset pagination over a non-unique key silently drops and duplicates
              rows across pages without this.

        The return type stays ``Iterable`` for consistency with the other reads and to let ORM
        backends return generators; use ``count_notifications`` when a total is needed.
        """
        ...

    def get_backend_identifier(self) -> str | None:
        """
        Return this backend's stable identifier for multi-backend routing.

        Concrete default returning ``None``: the owning ``AsyncIONotificationService``
        falls back to ``backend-{n}`` (``n`` being this backend's position among the
        service's configured backends) when a backend does not declare its own
        identifier. Override to return a stable, host-chosen identifier (e.g. a region or
        database alias) so routing does not shift if backends are reordered.

        Sync (not a coroutine) on purpose: resolving an identifier never needs I/O, and
        keeping it sync lets the constructor call it without an event loop.
        """
        return None

    async def apply_replication_snapshot_if_newer(
        self, snapshot: "Notification | OneOffNotification"
    ) -> ApplyResult:
        """Upsert the primary's snapshot into this backend when it is the newer record.

        Concrete default that declines every snapshot (``ApplyResult(applied=False)``): a
        backend that does not override this makes the owning ``AsyncIONotificationService``
        fall back to a read-then-write replica mutation. Override to implement an id-keyed,
        newer-wins upsert (comparing ``modified``) so inline replication can create a replica's
        copy with the primary's id, or refresh it, in a single call -- return
        ``ApplyResult(applied=True)`` once applied so the service skips the fallback, and
        ``applied=False`` (optionally with a ``reason``) to defer to it.

        Return ``ApplyResult(applied=False, superseded=True)`` when this backend's row is newer
        than the snapshot: the service then skips the fallback, since the row already reflects
        a later write, and replaying the older snapshot would overwrite it.

        ``snapshot`` is the primary backend's authoritative record for a notification; it must
        be written with its existing id, never a freshly assigned one. Concrete rather than
        abstract on purpose: multi-backend replication is opt-in, so forcing every backend to
        implement it would break single-backend deployments that never use it.

        A backend that keeps version tokens (see ``filter_notification_versions``) must store
        ``snapshot.notification_version`` as the row's token when it applies the snapshot,
        instead of issuing one of its own, and issue a new one only when the snapshot carries
        none. That is how a replica comes to hand out tokens the primary accepts as
        ``expected_version``. So a backend that reports ``versions.filter`` and is used as a
        replica must implement this or report ``writes.notificationVersion`` (see the class
        docstring): one that replays writes without taking the primary's token holds tokens of
        its own, which the primary always refuses, and the service reports ``versions.filter``
        as ``False`` for it when it is named.
        """
        return ApplyResult(
            applied=False,
            reason="apply_replication_snapshot_if_newer is not implemented by this backend",
        )

    async def get_all_notifications(self) -> Iterable["Notification | OneOffNotification"]:
        """
        Return every notification the backend holds, across all pages.

        Concrete default derived from ``filter_notifications({})`` by exhausting every
        page, so a backend that only implements the abstract ``filter_notifications``
        keeps working. Backends SHOULD override this for efficiency (e.g. an unpaginated
        query or a streaming cursor). Feeds multi-backend sync stats and migration.
        """
        results: "list[Notification | OneOffNotification]" = []
        page = 1
        page_size = 100
        while True:
            batch = list(await self.filter_notifications({}, page=page, page_size=page_size))
            if not batch:
                return results
            results.extend(batch)
            if len(batch) < page_size:
                return results
            page += 1

    async def get_filter_capabilities(self) -> dict[str, bool]:
        """
        Report which filter fields, string lookups, logical composition and sort fields
        this backend supports, and which pagination convention it follows.

        Keys are camelCase dotted (``'fields.notificationType'``, ``'orderBy.sentAt'``) and a
        backend declares ONLY what it *cannot* do -- the service merges this report OVER an
        all-``True`` default, so a missing key means supported. The concrete default returns
        ``{}`` (everything supported); backends override to decline specific capabilities.

        One key is a convention rather than a feature: ``'pagination.oneIndexed'`` defaults to
        ``True``, meaning ``page=1`` is the first page. A backend whose pages start at ``0``
        MUST report ``{'pagination.oneIndexed': False}``, because a caller that assumes the
        wrong base gets no error -- just the wrong rows. See
        ``DEFAULT_BACKEND_FILTER_CAPABILITIES``.

        The presence filters (``fields.hasSendAfter``, ``negation.hasSendAfter``,
        ``fields.hasRequestedTemplateVersion``, ``negation.hasRequestedTemplateVersion``) are
        the exception: they default to ``False``, so a backend that evaluates them reports
        ``True``.
        """
        return {}

    async def repin_notification(
        self,
        notification_id: int | str | uuid.UUID,
        template_version: int,
        *,
        expected_version: str | None = None,
        lock: asyncio.Lock | None = None,
    ) -> "Notification | OneOffNotification":
        """Pin a notification to a template version, in one conditional write where it can.

        The AsyncIO twin of ``BaseNotificationBackend.repin_notification``; see it for the
        contract. Optional, added in 3.8.0. This default reads the notification, checks its
        status and writes through ``persist_notification_update``. It cannot check
        ``expected_version``, so it raises ``UnsupportedNotificationActionError`` when given one.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        if expected_version is not None:
            raise UnsupportedNotificationActionError(
                f"Cannot repin notification {notification_id}: this backend does not check "
                "expected_version"
            )
        notification = await self.get_notification(notification_id)
        if notification.status not in REPINNABLE_NOTIFICATION_STATUSES:
            raise NotificationRepinError(
                f"Cannot repin notification {notification_id}: it has already been delivered"
            )
        if notification.requested_template_version == template_version:
            return notification
        return await self.persist_notification_update(
            notification_id, {"requested_template_version": template_version}, lock
        )

    async def filter_notification_versions(
        self,
        filter: "NotificationFilter",  # noqa: A002
        page: int,
        page_size: int,
    ) -> "list[NotificationVersion]":
        """The ID and version token of every notification matching ``filter``, a page at a time.

        The AsyncIO twin of ``BaseNotificationBackend.filter_notification_versions``; see it for
        the contract, including what an implementation must not read and filling
        ``notification_version`` on returned notifications. Optional, added in 3.8.0. This
        default raises ``UnsupportedNotificationActionError``.
        """
        raise UnsupportedNotificationActionError(
            f"Backend {self.get_backend_identifier() or type(self).__name__} does not support "
            "filter_notification_versions"
        )

    async def get_action_capabilities(self) -> dict[str, bool]:
        """Which optional notification actions this backend supports.

        The AsyncIO twin of ``BaseNotificationBackend.get_action_capabilities``; see it for the
        keys. Optional, added in 3.8.0. This default reports nothing.
        """
        return {}

    async def count_notifications(self, filter: "NotificationFilter") -> int:  # noqa: A002
        """
        Total number of notifications matching ``filter``, ignoring pagination.

        Concrete default derived from ``filter_notifications`` by exhausting every page, so a
        backend that only implements the abstract ``filter_notifications`` keeps working.
        Backends SHOULD override this for efficiency (e.g. a database ``COUNT``).
        """
        total = 0
        page = 1
        page_size = 100
        while True:
            batch = list(await self.filter_notifications(filter, page=page, page_size=page_size))
            total += len(batch)
            if len(batch) < page_size:
                return total
            page += 1

    @abstractmethod
    async def get_user_email_from_notification(
        self, notification_id: int | str | uuid.UUID
    ) -> str: ...

    @abstractmethod
    async def store_context_used(
        self,
        notification_id: int | str | uuid.UUID,
        context: dict,
        adapter_import_str: str,
        lock: asyncio.Lock | None = None,
    ) -> None:
        """Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    @abstractmethod
    async def store_git_commit_sha(
        self,
        notification_id: int | str | uuid.UUID,
        git_commit_sha: str,
        lock: asyncio.Lock | None = None,
    ) -> None:
        """Persist the git commit SHA that rendered and sent this notification.

        Called by AsyncIONotificationService at send time, only when the resolved SHA
        differs from what is already stored -- so an implementation need not deduplicate
        writes itself. ``git_commit_sha`` always arrives already normalized (40 lowercase
        hex characters).

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...

    async def store_template_version(
        self,
        notification_id: int | str | uuid.UUID,
        template_version: int,
        lock: asyncio.Lock | None = None,
    ) -> None:
        """Persist which version of the template actually rendered this notification.

        Called by AsyncIONotificationService at send time, only when the renderer reported a
        version and it differs from what is already stored -- so an implementation need not
        deduplicate writes itself.

        Concrete (not abstract) and a no-op on purpose, unlike ``store_git_commit_sha``: a
        backend with no column for it inherits this and keeps working, which is what lets the
        field be added without a major version. The cost of not overriding it is only that
        ``used_template_version`` stays None on the records that backend holds.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        return None

    def inject_attachment_manager(self, manager: "AsyncIOBaseAttachmentManager") -> None:
        """Store the attachment manager the service resolved for this backend.

        Concrete (not abstract) on purpose: a backend that does not do attachments needs
        no changes and simply never has a manager injected. ``supports_attachments`` uses
        the presence of this method to decide whether to inject at all, so a backend that
        predates the attachment seam -- and therefore lacks this method -- is skipped
        rather than erroring.
        """
        self._attachment_manager = manager

    @abstractmethod
    async def store_attachment_file_record(
        self, record: "AttachmentFileRecord", lock: asyncio.Lock | None = None
    ) -> "AttachmentFileRecord":
        """Persist a checksum-indexed file record and return it.

        The backend owns only the row; the bytes it describes were written by the
        injected attachment manager.
        """
        ...

    @abstractmethod
    async def get_attachment_file_record(self, file_id: str) -> "AttachmentFileRecord | None":
        """Return the file record with ``file_id``, or None if there is none."""
        ...

    @abstractmethod
    async def find_attachment_file_by_checksum(
        self, checksum: str, size: int
    ) -> "AttachmentFileRecord | None":
        """Return an existing file record matching both ``checksum`` and ``size``.

        Size is compared alongside the sha256 digest so that a digest collision degrades
        to a miss (a fresh upload) rather than silently serving the wrong file.
        """
        ...

    @abstractmethod
    async def delete_attachment_file(self, file_id: str, lock: asyncio.Lock | None = None) -> None:
        """Delete the file record with ``file_id``.

        Deleting the underlying bytes is a separate, manager-driven step; this only drops
        the row.
        """
        ...

    @abstractmethod
    async def get_orphaned_attachment_files(self) -> "Iterable[AttachmentFileRecord]":
        """Return file records no longer referenced by any notification join row."""
        ...

    @abstractmethod
    async def get_attachments(
        self, notification_id: int | str | uuid.UUID
    ) -> "Iterable[StoredAttachment]":
        """Return the stored attachments for a notification.

        Each file handle is rebuilt by handing the record's ``storage_identifiers`` back
        to the injected attachment manager.
        """
        ...

    @abstractmethod
    async def delete_notification_attachment(
        self, attachment_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None:
        """Delete a single notification attachment join row by its own id.

        Takes a keyword-only ``notification_version: str | None = None`` when the backend
        reports ``writes.notificationVersion``; see the class docstring.
        """
        ...


def supports_attachments(backend: "AsyncIOBaseNotificationBackend") -> bool:
    """Whether ``backend`` accepts an injected attachment manager.

    Duck-typed rather than an ``isinstance`` check so a backend that predates the
    attachment seam -- and therefore does not expose ``inject_attachment_manager`` -- is
    transparently treated as attachment-unaware instead of breaking.
    """
    return hasattr(backend, "inject_attachment_manager")
