import asyncio
import datetime
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterable

from vintasend.services.dataclasses import ApplyResult
from vintasend.services.utils import get_class_path


if TYPE_CHECKING:
    from vintasend.services.attachment_managers.asyncio_base import AsyncIOBaseAttachmentManager
    from vintasend.services.dataclasses import (
        AnyNotificationAttachment,
        AttachmentFileRecord,
        Notification,
        OneOffNotification,
        StoredAttachment,
        UpdateNotificationKwargs,
    )
    from vintasend.services.notification_backends.filters import (
        NotificationFilter,
        NotificationOrderBy,
    )


class AsyncIOBaseNotificationBackend(ABC):
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
        lock: asyncio.Lock | None = None,
    ) -> "Notification": ...

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
        lock: asyncio.Lock | None = None,
    ) -> "OneOffNotification": ...

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
        """
        ...

    @abstractmethod
    async def mark_pending_as_sent(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a pending notification as sent. Implementations must set ``sent_at`` to
        the current time on the affected row.
        """
        ...

    @abstractmethod
    async def mark_pending_as_failed(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification": ...

    @abstractmethod
    async def mark_sent_as_read(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> "Notification | OneOffNotification":
        """
        Mark a sent notification as read. Implementations must set ``read_at`` to
        the current time on the affected row.
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
        """
        ...

    @abstractmethod
    async def cancel_notification(
        self, notification_id: int | str | uuid.UUID, lock: asyncio.Lock | None = None
    ) -> None: ...

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

        ``snapshot`` is the primary backend's authoritative record for a notification; it must
        be written with its existing id, never a freshly assigned one. Concrete rather than
        abstract on purpose: multi-backend replication is opt-in, so forcing every backend to
        implement it would break single-backend deployments that never use it.
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
        Report which filter fields, string lookups and sort fields this backend supports.

        Keys are camelCase dotted (``'fields.notificationType'``, ``'orderBy.sentAt'``) and a
        backend declares ONLY what it *cannot* do -- the service merges this report OVER an
        all-``True`` default, so a missing key means supported. The concrete default returns
        ``{}`` (everything supported); backends override to decline specific capabilities.
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
    ) -> None: ...

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
        """
        ...

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
        """Delete a single notification attachment join row by its own id."""
        ...


def supports_attachments(backend: "AsyncIOBaseNotificationBackend") -> bool:
    """Whether ``backend`` accepts an injected attachment manager.

    Duck-typed rather than an ``isinstance`` check so a backend that predates the
    attachment seam -- and therefore does not expose ``inject_attachment_manager`` -- is
    transparently treated as attachment-unaware instead of breaking.
    """
    return hasattr(backend, "inject_attachment_manager")
