# Release Notes

## Version 2.0.0 (2026-07-23)

2.0 is a major release that bundles several feature sets: background notification sending through a
queue service, a composable filtering / ordering API, a dedicated attachment manager seam, git commit
SHA tracking, and rendering a notification from historical template content. The breaking changes
come from the background-sending rework and from new abstract methods that every downstream backend
and email renderer must implement. See `MIGRATION_TO_2.0.0.md` for step-by-step upgrade guidance.

### Features

#### Git commit SHA tracking
- New injected component: `BaseGitCommitShaProvider` (`vintasend.services.git_commit_sha_providers`)
  and its AsyncIO twin `AsyncIOBaseGitCommitShaProvider` expose a single method,
  `get_current_git_commit_sha() -> str | None`, that a host implements to report the git commit
  SHA of the revision currently running. It follows the same injection pattern as the queue
  service and attachment manager: an instance, a dotted import string, or the new
  `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` setting. Core ships the ABCs plus a reference fake,
  `FakeGitCommitShaProvider` / `FakeAsyncIOGitCommitShaProvider`; no default provider ships in
  core, and with none configured the feature is entirely off -- no SHA is ever resolved or
  written, and existing send/delayed_send flows are byte-for-byte unchanged.
- `Notification` and `OneOffNotification` gained a system-managed `git_commit_sha: str | None`
  field. Both `NotificationService` and `AsyncIONotificationService` resolve it at **send** time
  (not creation time) at the top of both `send()` and `delayed_send()`, so a scheduled
  notification records the revision that actually delivered it -- foreground or from a
  background worker. The provider is called on every send, but the resolved, normalized SHA
  (trimmed, lowercased, 40 hex characters) is only persisted when it differs from what is
  already stored, through a new dedicated backend method, `store_git_commit_sha`.
- A provider that raises is caught and logged, then treated exactly like a `None` return --
  audit metadata is never allowed to block a delivery. A provider returning a non-`None`,
  malformed value (not 40 hex characters once trimmed) raises `InvalidGitCommitShaError`.
- `git_commit_sha` is system-managed: it cannot be set through `create_notification`, and
  `update_notification` raises the new `GitCommitShaReassignmentError` if a caller passes it,
  mirroring the existing `tenant` reassignment guard. It is only ever written by the service
  itself, through `store_git_commit_sha`, while the row is still pending.
- See the README's "Git Commit SHA Tracking" section.

#### Background sending via a queue service
- Adapters opt in to background delivery by subclassing `BackgroundNotificationAdapter` (sync) or
  `AsyncIOBackgroundNotificationAdapter` (AsyncIO). When a background-capable adapter is used,
  `send()` enqueues the notification id to the configured queue service and returns immediately; a
  worker calls the service's `delayed_send(notification_id)` to deliver it. This decouples web
  request latency from notification delivery.
- The queue now carries only the notification id, not serialized notification data. The worker
  reloads the notification from the backend, so context is generated at delivery time (not at
  enqueue time) and attachments work on the background path for the first time.
- `NOTIFICATION_SERVICE_FACTORY` points to a callable that returns a ready `NotificationService`
  or `AsyncIONotificationService` for the worker. The factory runs once per process and the result
  is cached, enabling ORM sessions scoped to the worker rather than rebuilt per task.
- `AsyncIONotificationService` supports background sending via `AsyncIOBackgroundNotificationAdapter`
  and `AsyncIOBaseNotificationQueueService`.

#### Filtering, ordering, and resend
- `filter_notifications(filter, page, page_size, order_by=None)`, `count_notifications(filter)` and
  `get_backend_supported_filter_capabilities()` on both services. The composable filter vocabulary
  lives in `vintasend.services.notification_backends.filters`: field filters (scalar equality or
  list membership), string lookups (`exact` / `starts_with` / `ends_with` / `includes`, case
  sensitivity configurable), inclusive date ranges, and `and` / `or` / `not` groups that nest
  arbitrarily. An empty filter matches every notification. `get_backend_supported_filter_capabilities`
  reports which fields, lookups and sort fields the configured backend supports, so a client can
  grey out what it can't use. See the README's "Filtering and Ordering Notifications" section.
- `resend_notification(notification_id, use_stored_context_if_available=False)` on both services:
  clones a sent notification into a brand-new pending row and sends it immediately, leaving the
  original untouched. Refuses one-off notifications and notifications still scheduled in the future
  by raising `NotificationResendError`. `use_stored_context_if_available=True` reuses the source's
  stored context verbatim instead of regenerating it through the context registry.
- `Notification` and `OneOffNotification` gained `sent_at`, `read_at` and `tenant` fields
  (`datetime | None` / `datetime | None` / `str | None`, all defaulting to `None`).
  `mark_pending_as_sent` sets `sent_at`; `mark_sent_as_read` and `mark_sent_as_read_bulk` set
  `read_at`. `persist_notification` and `persist_one_off_notification` gained an optional `tenant`
  keyword. The filter vocabulary includes `sent_at_range`, `read_at_range` and `tenant` (equality
  or membership).

#### Render a notification from historical template content
- `render_email_template_from_content(notification, template_content, context)` on both services:
  given a notification, an `EmailTemplateContent` (`subject_template`, `body_template`, optional
  `preheader_template`), and a context -- typically a notification's stored `context_used` -- renders
  and returns the resulting `TemplatedEmail` without sending or persisting anything. This is a
  read-shaped preview/audit operation: no context is generated (the caller supplies it verbatim) and
  the notification's own stored templates are never consulted. Raises the new
  `NotificationRenderError` when the notification's type has no email adapter configured, or its
  configured renderer is not a `BaseTemplatedEmailRenderer`. See the README's "Rendering a
  notification from historical template content" section, including its injection-safety caveat.
- New abstract method `render_from_template_content` on `BaseTemplatedEmailRenderer`, mirroring
  `render`'s signature with an `EmailTemplateContent` replacing the stored template reference. The
  reference implementation, `FakeTemplateRenderer`, renders the supplied content directly. See
  "Breaking Changes" and "Backwards Compatibility" below.
- `TemplatedEmail` gained an optional `preheader: str | None = None` field, additive with a default,
  so it reproduces a historical preheader when one was supplied.

#### Attachment manager seam
- New attachment manager seam: `BaseAttachmentManager` and `AsyncIOBaseAttachmentManager`
  (`vintasend.services.attachment_managers`) own every byte of attachment storage —
  `upload_file`, `reconstruct_attachment_file`, and `delete_file_by_identifiers` — so a
  notification backend never reads, writes, or downloads a file itself. Core ships the ABCs plus a
  working reference, `FakeAttachmentManager` / `FakeAsyncIOAttachmentManager`; real managers (local
  disk, S3, Django storage, and so on) live in their own `vintasend-*` package. See
  [ATTACHMENTS.md](ATTACHMENTS.md).
- Both services take a new `attachment_manager` constructor argument (instance, dotted import
  string, or the new `NOTIFICATION_ATTACHMENT_MANAGER` setting), injected into the backend through
  the duck-typed `inject_attachment_manager` hook. A backend that predates this seam and has no
  such method is left untouched and keeps working with no attachment support.
- The attachment model is now checksum-indexed: `AttachmentFileRecord` describes one stored blob,
  and `StoredAttachment` is the join row linking a notification to it. `storage_metadata` is kept
  as a deprecated alias for the new `storage_identifiers` field.
- `NotificationAttachmentReference(file_id=...)` attaches an already-uploaded file by id instead of
  re-uploading it. `NotificationAttachment` (an upload) and `NotificationAttachmentReference` (a
  reference) are both accepted through the new `AnyNotificationAttachment` union, distinguished with
  the new `is_attachment_reference` type guard. Identical uploads are deduplicated on checksum and
  size; a reference to an unknown `file_id` raises the new `AttachmentFileNotFoundError`.
- `get_orphaned_attachment_files` returns file records no longer referenced by any notification, for
  a caller-driven, two-step reclamation. Nothing is deleted automatically.

### Bug Fixes
- `FakeFileBackend` and `FakeAsyncIOFileBackend` now stamp `created` and `modified` when a
  notification is persisted, and advance `modified` on updates and status transitions — matching a
  real ORM's `auto_now_add` / `auto_now`. Previously both fields stayed `None` for any notification
  created through the service, so `created_at_range` filters matched nothing and the default
  `created`-descending ordering fell through to the `id` tiebreaker.

### Breaking Changes

1. **`raise_on_failed_send` defaults to `False`.** In 1.x, send failures raised
   `NotificationSendError` and similar exceptions. In 2.0 they are logged but not raised by default.
   Applications that catch these exceptions should pass `raise_on_failed_send=True` to
   `NotificationService` / `AsyncIONotificationService` to restore 1.x behavior.
2. **Background adapter `delayed_send` signature changed.** The adapter marker method now takes only
   `notification_id`, not `(notification_dict, context_dict)`. Core never calls this method;
   delivery happens via the adapter's `send()` after the worker loads the notification. Adapter
   authors must move background delivery logic from `delayed_send` to `send()`.
3. **Deleted serialization hooks and types.** The eight abstract serialize/restore methods on
   `AsyncNotificationProtocol`, plus the types `NotificationDict` and `OneOffNotificationDict`, are
   deleted. No serialization is needed with id-only payloads.
4. **`NOTIFICATION_SERVICE_FACTORY` is required for background sending.** The worker needs a factory
   callable to rebuild the service in its own process. Web and worker must also share the same
   `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE` settings, or the worker silently fails to
   find notifications.
5. **Adapter rename.** `AsyncBaseNotificationAdapter` is renamed to `BackgroundNotificationAdapter`;
   the old name is kept as a silent alias for compatibility. New AsyncIO background adapters
   subclass `AsyncIOBackgroundNotificationAdapter`.
6. **New abstract methods on both backend base classes — every custom backend subclass MUST
   implement them before it can be instantiated against 2.0:**
   - From the filtering API: `filter_notifications`. (`get_filter_capabilities` and
     `count_notifications` are concrete defaults, so they need no changes but SHOULD be overridden
     for efficiency.)
   - From the attachment seam: `store_attachment_file_record`, `get_attachment_file_record`,
     `find_attachment_file_by_checksum`, `delete_attachment_file`, `get_orphaned_attachment_files`,
     `get_attachments`, and `delete_notification_attachment`. (`inject_attachment_manager` is added
     too, but concrete with a default, so it needs no change.)
7. **New abstract method on `BaseTemplatedEmailRenderer` — every custom email renderer subclass MUST
   implement `render_from_template_content` before it can be instantiated against 2.0.** See
   "Backwards Compatibility" below.

### New exceptions
- `TenantReassignmentError`: raised by `update_notification` when `tenant` appears in the update
  kwargs. `tenant` cannot be changed after a notification is created.
- `NotificationResendError`: raised by `resend_notification` for a one-off notification or one still
  scheduled in the future.
- `AttachmentFileNotFoundError`, `AttachmentUploadError`, and `UnsupportedAttachmentFileTypeError`
  for the attachment paths.
- `NotificationRenderError`: raised by `render_email_template_from_content` when the notification's
  type has no email adapter configured, or its configured renderer is not a
  `BaseTemplatedEmailRenderer`. Distinct from the existing `NotificationTemplateRenderingError`
  family, which covers a renderer failing while actually rendering a template it was handed.
- All derive from `NotificationError`, which derives from `ValueError`, so existing
  `except ValueError` handlers keep working.

### Backwards Compatibility
- **`AsyncBaseNotificationAdapter` is now an alias** for `BackgroundNotificationAdapter`. Existing
  imports keep working; `BackgroundNotificationAdapter` is the recommended name for new code.
- **`raise_on_failed_send` silent behavior change.** Code that does not catch send exceptions sees
  the same behavior (failures logged). Code that catches `NotificationSendError` must pass
  `raise_on_failed_send=True` to restore 1.x semantics.
- The `sent_at`, `read_at` and `tenant` fields are additive with `None` defaults, appended after the
  existing ones, so existing positional construction and existing callers are unaffected. The
  optional trailing `tenant` keyword on `persist_notification` / `persist_one_off_notification` is
  forwarded only when a caller passes it, so a backend built against a pre-2.0 signature keeps
  working for tenant-less callers.
- `update_notification` now raises `TenantReassignmentError` if `tenant` is present in the update
  kwargs (checked on the raw dict). `send()` gained an optional trailing `context` keyword used
  internally by `resend_notification`; every existing caller passes no `context` and is unaffected.
- `UpdateNotificationKwargs.attachments` intentionally stays typed `list[StoredAttachment]`, not
  widened to `AnyNotificationAttachment`, because `persist_notification_update` has no upload path.
- **Downstream packages.** `vintasend-celery` is significantly affected and requires a 2.0 release
  of its own. `vintasend-django` must implement the new filter and attachment seams (plus a schema
  migration for the new attachment table) before it can pin `vintasend>=2.0.0`. `vintasend-sqlalchemy`
  cannot adopt this release until its own catch-up plan lands, since it is already missing methods
  from 1.2.0.
- **New abstract method**: `store_git_commit_sha(notification_id, git_commit_sha)` was added to
  `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`. Every downstream backend
  implementation MUST implement it before it can be instantiated -- a subclass missing it raises
  `TypeError` at construction. This repo releases first; `vintasend-django` follows with a
  matching release that widens its `vintasend` pin. `vintasend-sqlalchemy` adopts it as part of
  its ongoing catch-up plan.
- **New abstract method**: `render_from_template_content(notification, template_content, context)`
  was added to `BaseTemplatedEmailRenderer`. Every downstream email renderer implementation MUST
  implement it before it can be instantiated -- a subclass missing it raises `TypeError` at
  construction. `vintasend-django` and `vintasend-jinja` are the two affected packages; both must
  implement it and widen their `vintasend` pin before they can be released against `vintasend>=2.0.0`.
  This repo releases first. Backends, adapters, and non-email renderers are untouched.
- With no `git_commit_sha_provider` configured (the default), `send()`, `delayed_send()`,
  `create_notification()`, and `update_notification()` behave exactly as before this release -- that
  feature is additive for every caller who does not opt in. Likewise,
  `render_email_template_from_content` is a wholly new, opt-in entrypoint: no existing method's
  signature or semantics changed to support it, and no notification is sent or persisted by calling
  it.

### Operational Requirements
- **Drain or dual-register the Celery queue before deploying.** Tasks queued under 1.x carry a
  different payload format and will fail against 2.0. Either drain the queue before deploying the
  2.0 worker or register the new entrypoint under a new task name and run both workers until the old
  queue empties. See `MIGRATION_TO_2.0.0.md`.

### Upgrade Path
1. Read `MIGRATION_TO_2.0.0.md` for the breaking changes and the deploy procedure.
2. If you use background sending, set up `NOTIFICATION_SERVICE_FACTORY`.
3. If you maintain an adapter, move to `BackgroundNotificationAdapter` /
   `AsyncIOBackgroundNotificationAdapter` and move `delayed_send` logic to `send()`.
4. If you maintain a backend, implement the new filter and attachment abstract methods.
5. If you maintain an email template renderer, implement `render_from_template_content`.
6. Test end-to-end, including attachments in background sends (now supported), then drain the queue
   and deploy the 2.0 worker.

## Version 1.4.0 (2026-07-22)

### Bug Fixes
- `NotificationService` and `AsyncIONotificationService` now reject two or more adapters that
  declare the same `notification_type`, raising the new
  `vintasend.exceptions.DuplicateNotificationAdapterError` at construction. Previously both
  adapters were kept, and because the send loop has no `break`, every notification of that type
  was sent twice: the second `mark_pending_as_sent` then failed because the row was no longer
  `PENDING_SEND`, and if the first adapter failed while the second succeeded the notification was
  marked FAILED and then overwritten as SENT. The error message names the offending notification
  type and the `adapter_import_str` of every adapter declaring it.
- `create_one_off_notification` now validates `email_or_phone` before anything is persisted, on
  both services, raising the new `vintasend.exceptions.InvalidOneOffNotificationRecipientError`.
  An empty string, a whitespace-only string, or a value that is neither an email address nor a
  10-to-15-digit phone number (optionally `+`-prefixed) previously persisted a notification that
  could never be delivered. Validation is on format only; it does not check deliverability. Both
  new exceptions derive from `NotificationError`, which derives from `ValueError`, so existing
  `except ValueError` handlers keep working.

### Backwards compatibility
- No seam method was added, renamed, or removed, and no existing method signature or semantic
  changed. Custom backends, adapters and template renderers need no code changes, and the
  `vintasend-django`, `vintasend-sqlalchemy`, `vintasend-celery`, and renderer/adapter packages
  need no release.
- **An application that configures two adapters for the same notification type now fails at
  service construction instead of starting.** This is deliberate -- that configuration was
  double-sending every notification of that type and corrupting its status -- but the failure
  appears at deploy time rather than at upgrade time. The remedy is to remove the duplicate
  adapter from `NOTIFICATION_ADAPTERS` (or from the `notification_adapters` argument). The error
  message names the type and every adapter declaring it, so it tells you exactly what to delete.
- **Custom adapters must set `notification_type` to a `NotificationTypes` member.** It has always
  been declared that way on `BaseNotificationAdapter` and `AsyncIOBaseNotificationAdapter`, but it
  is not an `@abstractmethod`, so an adapter that omitted it or declared it as a plain `str`
  previously failed only at send time. Service construction now reads it, so such an adapter fails
  earlier and with an `AttributeError` rather than a `NotificationError`.
- **Callers passing an empty or malformed `email_or_phone` to `create_one_off_notification` now
  get an exception where they previously got a persisted notification.** Those notifications were
  never deliverable. Existing rows are untouched -- validation is on the create path only, and
  `update_notification` is unchanged.

## Version 1.3.0 (2026-07-22)

### Bug Fixes
- `AsyncIONotificationService` now accepts the same `(import_str, kwargs)` adapter tuple form
  that `NotificationService` already accepted, for example
  `notification_adapters=[(("pkg.Adapter", {"k": 1}), "pkg.Renderer")]`. The async construction
  helper, `get_asyncio_notification_adapters`, already handled this shape; the service's own
  validation guard did not, and rejected it with `NotificationError("Invalid notification
  adapters")` before the helper ever ran.

### Internal Improvements
- Extracted the file-attachment and context-function helpers duplicated between `NotificationService`
  and `AsyncIONotificationService` into `vintasend.services.service_utils`; both classes now delegate
  to one shared implementation of each. No public signature changed.
- Closed sync/AsyncIO parity gaps between the two services: `AsyncIONotificationService.send_pending_notifications`
  now tracks sent/failed counters and logs the same summary lines its sync twin,
  `NotificationService.send_pending_notifications`, always has; `AsyncIONotificationService.__init__`
  now initializes the `NotificationSettings` singleton up front, matching `NotificationService.__init__`.
- Importing `vintasend.services.notification_service` no longer imports `requests` as a side effect --
  `download_from_url` now imports it lazily, at call time, with a friendly `ImportError` if it's
  missing.

### Backwards compatibility
- `AsyncIONotificationService.__init__` now constructs the `NotificationSettings` singleton
  immediately, matching `NotificationService.__init__`. `NotificationSettings` is a singleton:
  the first construction wins, and every later `config` argument is ignored. An application
  that builds `AsyncIONotificationService(...)` with `config=None` before anything else
  constructs `NotificationSettings` with a real config will now get the `None`-derived
  settings everywhere, where previously the in-request construction with the real config
  would have won. Build the service after your config is available, or pass `config` to the
  service, rather than relying on some other later construction of `NotificationSettings` to
  supply it.
- A host application that relied on the transitive `requests` import (see the `requests` bullet
  above) instead of depending on `requests` itself will see that `ImportError` move from import
  time to call time. `requests` remains a declared runtime dependency in `pyproject.toml`, so
  this affects nobody who installs the package normally.

## Version 1.2.0 (2026-06-14)

### Features

* List ALL in-app notifications (read + unread) on the backend ABCs and services:
  `filter_all_in_app_notifications` (unpaginated) and `filter_in_app_notifications(page, page_size)`
  (paginated). "All" = `notification_type == IN_APP` and `status in (SENT, READ)`; internal
  pipeline states (PENDING_SEND, FAILED, CANCELLED) are never exposed to end users.
* Count helpers `count_in_app_notifications` and `count_in_app_unread_notifications` on the
  backend ABCs (concrete defaults derived from the existing iterables; backends SHOULD override
  for efficiency), and `get_in_app_notifications_count` / `get_in_app_unread_count` on the
  services. Combined with the paginated list methods these let callers build
  count / next / previous envelopes for both the unread and the all lists.
* Service method `get_in_app_notifications(user_id, page=1, page_size=10)` mirroring
  `get_in_app_unread` (including the "No in-app notification adapter found" guard).
* Bulk mark-as-read: `mark_sent_as_read_bulk(notification_ids, user_id=None)` on the backend
  ABCs and `mark_read_bulk(notification_ids, user_id=None)` on the services. Idempotent
  (already-READ / missing / not-owned / non-SENT ids are skipped, never an error), optionally
  scoped to `user_id` (rows owned by others are never touched), and returns the final READ state
  for the requested ids.
* `Notification` and `OneOffNotification` dataclasses gained optional `created` and `modified`
  timestamp fields (`context_used` already existed), defaulting to `None` so existing
  constructors and non-Django backends keep working.

### Backwards compatibility

* Three new abstract methods were added to `BaseNotificationBackend` and
  `AsyncIOBaseNotificationBackend`: `filter_all_in_app_notifications`,
  `filter_in_app_notifications`, and `mark_sent_as_read_bulk`. Custom backend subclasses MUST
  implement them. The two `count_*` methods are concrete defaults, so they require no changes
  but SHOULD be overridden for efficiency.
* No existing method signature or semantic changed; this is an additive minor release.

## Version 1.1.3 (2026-06-03)
- Bumped version to follow the officially-supported implementations

## Version 1.1.2 (2026-06-03)
- Fixed bug in periodic_send_pending_notifications. We were only sending notifications if the first adapter configured was async, now we're searching through the list.

## Version 1.1.1 (2026-06-03)

### Bug Fixes
- Replaced deprecated `asyncio.iscoroutinefunction` with `inspect.iscoroutinefunction` (removal slated for Python 3.16)

### Build Improvements
- Widened Python constraint to `<3.15` and added `py314` to the tox envlist for full Python 3.14 support

## Version 1.1.0 (2026-06-03)

### Build Improvements
- Added Python 3.14 to the CI and tox test matrix
- Bumped publish workflow to Python 3.13 for stable releases
- Pinned local Python version via `.python-version`

### Dependencies
- Updated project dependencies (`pyproject.toml` / `poetry.lock`)

## Version 1.0.1 (2025-09-16)

### Bug Fixes
- **Fixes bug on async adapters**: The instanciation of the service with strings wasn't enabling using adapters with kwargs

### Build Improvements
- Simplified publish workflow
- Fix duplicate runs on every push


## Version 1.0.0 (2025-09-16)

### 🚀 Major Features

#### File Attachments Support
- **NEW**: Added comprehensive file attachment support for notifications
- **Multiple Input Types**: Support for file paths, URLs, bytes data, file-like objects, and Path objects  
- **URL Downloads**: Automatic download of remote files from HTTP/HTTPS, S3, Google Cloud Storage, and Azure Blob Storage URLs
- **Content Type Detection**: Automatic MIME type detection based on file extensions
- **Inline Attachments**: Support for inline images in HTML emails with `is_inline` flag
- **Backend Integration**: New storage interfaces for backends to implement attachment persistence
- **Adapter Integration**: Updated adapter interfaces to handle attachments in email sending

#### One-Off Notifications
- **NEW**: Send notifications directly to email addresses or phone numbers without requiring user IDs
- **Direct Targeting**: Use email addresses or phone numbers as direct targets
- **Use Cases**: Perfect for welcome emails, marketing campaigns, and external party notifications
- **Full Feature Support**: One-off notifications support all standard features including attachments, scheduling, and templating

### 🔧 API Enhancements

#### Notification Service
- Added `attachments` parameter to `create_notification()` method
- Added `attachments` parameter to `create_one_off_notification()` method  
- Added `attachments` parameter to `update_notification()` method
- New `create_one_off_notification()` method for direct email/phone targeting
- Enhanced AsyncIO support for all new features

#### Data Classes
- **NEW**: `NotificationAttachment` class for defining file attachments
- **NEW**: `StoredAttachment` class for backend-stored attachment metadata
- **NEW**: `OneOffNotification` class for non-user-targeted notifications
- **NEW**: `FileAttachment` type alias supporting multiple input formats
- **NEW**: `AttachmentFile` abstract base class for stored file access

#### Backend Interfaces
- Added attachment storage methods to `BaseNotificationBackend`
- Added one-off notification persistence to backend interfaces
- Enhanced AsyncIO backend interfaces with attachment support
- New abstract methods for attachment lifecycle management

#### Adapter Interfaces  
- Enhanced adapter interfaces to handle attachments in notification sending
- Updated template renderer interfaces for attachment-aware rendering
- Backward compatible changes with optional attachment parameters

### 🔄 Backward Compatibility
- All existing APIs remain fully functional
- Optional attachment parameters maintain backward compatibility
- Existing notifications continue to work without modification
- No breaking changes to core interfaces

### 🧪 Testing & Quality
- Comprehensive test suite for attachment functionality (1300+ test lines)
- Tests for all file input types and edge cases
- AsyncIO and sync testing coverage
- Validation and error handling test cases
- End-to-end attachment workflow testing

### 📚 Documentation
- Updated README with attachment examples and usage patterns
- New glossary entries for attachments and one-off notifications
- AsyncIO examples for all new features
- Import statements updated for new classes

### 🔧 Dependencies & Infrastructure
- Updated setuptools dependency for security improvements
- Enhanced type hints and type safety
- Improved error handling and validation
- Added comprehensive docstrings for new features

### 📋 Migration Guide
For backend and adapter package maintainers:
- See `MIGRATION_TO_1.0.0.md` for detailed implementation guidance
- New abstract methods need implementation in external packages
- Stub implementations provided as reference
- Backward compatibility maintained for gradual migration

---

## Version 0.1.4 (Initial Release)

Initial version of VintaSend with core notification functionality.
