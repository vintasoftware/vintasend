# Release Notes

## Version 2.0.0 (2026-07-22)

### Features

* **Background notification sending via queue service:** Adapters now opt in to background delivery by subclassing `BackgroundNotificationAdapter` (sync) or `AsyncIOBackgroundNotificationAdapter` (AsyncIO). When a background-capable adapter is used, `send()` enqueues the notification id to the configured queue service and returns immediately; a worker calls the service's `delayed_send(notification_id)` to actually deliver it. This decouples web request latency from notification delivery.
* **ID-only queue payloads:** The queue now carries only the notification id, not serialized notification data. The worker reloads the notification from the backend, which means context is generated at delivery time (not at enqueue time) and attachments work on the background path for the first time.
* **Context generation in the worker:** Scheduled notifications render against current data when delivered, not against data from the enqueueing process, which is strictly more accurate.
* **Service factory for worker processes:** `NOTIFICATION_SERVICE_FACTORY` points to a callable that returns a ready `NotificationService` or `AsyncIONotificationService` for the worker. The factory runs once per process and the result is cached, enabling ORM sessions scoped to the worker rather than rebuilt per task.
* **AsyncIO background sending:** `AsyncIONotificationService` now supports background sending via `AsyncIOBackgroundNotificationAdapter` and `AsyncIOBaseNotificationQueueService`.

### Bug Fixes

None — this is a new feature set.

### Breaking Changes

1. **`raise_on_failed_send` defaults to `False`.** In 1.x, send failures raised `NotificationSendError` and similar exceptions. In 2.0, they are logged but not raised by default. Applications that catch these exceptions should pass `raise_on_failed_send=True` to `NotificationService` / `AsyncIONotificationService` to restore 1.x behavior.

2. **Background adapter `delayed_send` signature changed:** The adapter marker method now takes only `notification_id`, not `(notification_dict, context_dict)`. Core never calls this method; delivery happens via the adapter's `send()` method after the worker loads the notification. Adapter authors must move background delivery logic from `delayed_send` to `send()`.

3. **Deleted serialization hooks and types:** The eight abstract methods `serialize_backend_config`, `restore_backend_config`, and six others on `AsyncNotificationProtocol`, plus the types `NotificationDict` and `OneOffNotificationDict`, are deleted. No serialization is needed with id-only payloads.

4. **`NOTIFICATION_SERVICE_FACTORY` is required for background sending:** The worker needs a factory callable to rebuild the service in its own process. Without it, background-capable adapters work in the web process but enqueue with no queue service, which logs an error or raises (depending on `raise_on_failed_send`).

5. **Worker and web must share `NOTIFICATION_BACKEND` settings:** A worker with a different backend silently fails to find notifications.

6. **Adapter rename:** `AsyncBaseNotificationAdapter` is renamed to `BackgroundNotificationAdapter`; the old name is kept as a silent alias for compatibility. New AsyncIO background adapters subclass `AsyncIOBackgroundNotificationAdapter`.

### Backwards Compatibility

* **Seam changes:** No new abstract methods were added; only `delayed_send` on background adapters was reshaped. Custom backends and adapters need code changes only if they implement background sending.
* **`AsyncBaseNotificationAdapter` is now an alias.** Existing imports continue to work, but `BackgroundNotificationAdapter` is the recommended name for new code.
* **`raise_on_failed_send` silent behavior change:** Existing code that does not catch send exceptions sees the same behavior (failures logged). Code that catches `NotificationSendError` will stop seeing it and must pass `raise_on_failed_send=True` to restore 1.x semantics.
* **Downstream impact:** `vintasend-celery` is significantly affected and requires a 2.0 release of its own (separate repository). `vintasend-django` and `vintasend-sqlalchemy` are unaffected unless they implement background adapters. See `MIGRATION_TO_2.0.0.md` for detailed guidance on the `NOTIFICATION_SERVICE_FACTORY` pattern.

### Operational Requirements

* **Drain or dual-register the Celery queue before deploying:** Tasks queued under 1.x carry a different payload format and will fail against 2.0. Either drain the queue before deploying the 2.0 worker or register the new entrypoint under a new task name and run both workers until the old queue empties.

### Upgrade Path

1. Start by reading `MIGRATION_TO_2.0.0.md` for the six breaking changes and the deploy procedure.
2. If you use background sending, set up `NOTIFICATION_SERVICE_FACTORY` pointing to a factory that returns a ready service.
3. If you maintain an adapter, update it to the new `BackgroundNotificationAdapter` / `AsyncIOBackgroundNotificationAdapter` base and move `delayed_send` logic to `send()`.
4. Test end-to-end, including attachments in background sends (now supported).
5. Drain the queue and deploy the 2.0 worker.

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
