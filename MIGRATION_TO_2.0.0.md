# Migration to VintaSend 2.0.0

VintaSend 2.0 bundles three feature sets into one major release: background sending with a queue
service, a composable filtering / ordering API, and a dedicated attachment manager seam. The
breaking changes come from the background-sending rework (silent behavior changes, adapter reshape,
deleted serialization hooks) and from new abstract methods that every downstream backend must
implement. This guide covers each breaking change and the deploy procedure.

If you only consume the notification services (you do not maintain a custom backend or adapter), the
sections that affect you are **1** (`raise_on_failed_send`), **4** (`NOTIFICATION_SERVICE_FACTORY`,
only if you use background sending), and **7** (queue drain). If you maintain a backend or adapter,
read all of it — section **9** covers the new backend abstract methods added by the filtering and
attachment features.

## 1. Silent Behavior Change: `raise_on_failed_send` Defaults to `False`

**This is the most important migration point.** In 1.x, when a notification fails to send,
`NotificationSendError` was raised. In 2.0, it is logged but not raised by default — notification
failures no longer propagate to the caller.

If your application catches `NotificationSendError` or similar exceptions to log, alert, or handle
failures:

```python
# 1.x: exception was raised by default
try:
    service.send(notification)
except NotificationSendError as e:
    logger.error("Notification send failed: %s", e)
```

Restore 1.x behavior by passing `raise_on_failed_send=True`:

```python
# 2.0: pass raise_on_failed_send=True to restore 1.x behavior
service = NotificationService(
    notification_adapters=...,
    notification_backend=...,
    raise_on_failed_send=True,  # Restore 1.x: raise on send failures
)
```

If you do not catch these exceptions, no code change is needed — the notification is logged either
way.

## 2. Service-Level `delayed_send()` Signature Change

In 1.x, adapters declared background support by subclassing `AsyncBaseNotificationAdapter` and
implementing `delayed_send(notification_dict, context_dict)`. This method WAS the background delivery
method: the worker called `NotificationService.delayed_send(notification_dict, context_dict)`, which
called the adapter's `delayed_send(notification_dict, context_dict)` to do the actual send.
Serializing the notification and context into the queue payload meant attachments could not work in
background sends.

In 2.0, delivery moves to the adapter's `send()`. The service-level `delayed_send(notification_id)`
loads the notification, generates context in the worker, and calls the adapter's `send()` — the same
method the foreground path uses. The adapter's own `delayed_send(notification_id)` becomes a pure
marker method that core never calls; only its presence matters, to declare the adapter as
background-capable.

**If you maintain an adapter** that subclasses `AsyncBaseNotificationAdapter` (now
`BackgroundNotificationAdapter`):

```python
# 1.x adapter
class MyBackgroundAdapter(AsyncBaseNotificationAdapter):
    def send(self, notification, context):
        # Foreground delivery
        pass

    def delayed_send(self, notification_dict, context_dict):
        # 1.x: this was the actual background delivery method
        # The worker called service.delayed_send(...), which called this
        self._send_email(notification_dict, context_dict)
```

Move your 1.x `delayed_send` body into `send()`, which the worker now calls:

```python
# 2.0 adapter
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter

class MyBackgroundAdapter(BackgroundNotificationAdapter):
    def send(self, notification, context):
        # 2.0: both foreground and background call this
        # (foreground calls it during send(), background calls it during delayed_send())
        self._send_email(notification, context)

    def delayed_send(self, notification_id):
        # 2.0: this is now a marker only; core never calls it
        # The body was moved to send() above
        pass
```

The old `AsyncBaseNotificationAdapter` name is kept as a silent alias, so imports do not break:

```python
# Still works (silently)
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter
# But you should use the new name:
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
```

For AsyncIO adapters, subclass the new `AsyncIOBackgroundNotificationAdapter`:

```python
from vintasend.services.notification_adapters.asyncio_background_base import AsyncIOBackgroundNotificationAdapter

class MyAsyncIOBackgroundAdapter(AsyncIOBackgroundNotificationAdapter):
    async def send(self, notification, context):
        await self._send_email(notification, context)

    async def delayed_send(self, notification_id):
        pass  # Marker only
```

## 3. Deleted Serialization Hooks and Types

In 1.x, the queue payload carried the entire serialized notification so the worker could rebuild it
without accessing the database. This required eight abstract methods on `AsyncNotificationProtocol`:
`serialize_config`, `restore_config`, `serialize_backend_kwargs`, `restore_backend_kwargs`,
`serialize_adapter_kwargs`, `restore_adapter_kwargs`, `serialize_template_renderer_kwargs`,
`restore_template_renderer_kwargs`. These and the types they used — `NotificationDict` and
`OneOffNotificationDict` — are deleted in 2.0.

With the id-only payload, the queue carries nothing but a number; the worker reloads the notification
from the backend. No serialization is needed.

**If you implemented these methods** in an adapter subclass, delete them:

```python
# 1.x: these methods existed
class MyAdapter(AsyncBaseNotificationAdapter):
    def serialize_config(self, config):
        return json.dumps(config)

    def restore_config(self, serialized):
        return json.loads(serialized)

    # ... six more serialize/restore methods ...
```

Delete all eight — they have no 2.0 equivalent:

```python
# 2.0: delete serialize_* and restore_* methods
class MyAdapter(BackgroundNotificationAdapter):
    def send(self, notification, context):
        pass
```

## 4. `NOTIFICATION_SERVICE_FACTORY` Requirement for Background Sending

In 1.x, a background adapter enqueued a serialized notification, and the worker reconstructed it from
the payload. In 2.0, the worker needs to rebuild the service from scratch to access a live database
session or connection pool.

If your application uses background sending (any adapter subclasses `BackgroundNotificationAdapter`
or `AsyncIOBackgroundNotificationAdapter`), point `NOTIFICATION_SERVICE_FACTORY` to a callable that
returns a ready `NotificationService` or `AsyncIONotificationService`.

The factory is called once per worker process and the result is cached, so it must be safe to call
once per process and the service must be safe to reuse across tasks.

### Example: SQLAlchemy-backed factory

If you use `vintasend-sqlalchemy` with a SQLAlchemy session:

```python
# myapp/worker.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from vintasend.services.notification_service import NotificationService
from vintasend_sqlalchemy.backends import SQLAlchemyNotificationBackend

# Engine is process-scoped; sessionmaker is reusable
_engine = create_engine("postgresql://...")
_SessionLocal = sessionmaker(bind=_engine)

def notification_service_factory():
    """Build the notification service for this worker process."""
    session = _SessionLocal()
    backend = SQLAlchemyNotificationBackend(session)
    return NotificationService(
        notification_backend=backend,
        notification_adapters=[...],  # Your adapters
    )
```

Then set the environment variable or framework setting:

```bash
# Django settings.py
NOTIFICATION_SERVICE_FACTORY = "myapp.worker.notification_service_factory"

# Or as env var
export NOTIFICATION_SERVICE_FACTORY="myapp.worker.notification_service_factory"
```

For FastAPI or other frameworks without a global settings module, pass `config` to your service or
set the env var only.

**The worker and web process must read the same `NOTIFICATION_BACKEND` and
`NOTIFICATION_QUEUE_SERVICE` settings.** A worker pointed at a different backend will not find the
notification the id refers to, and background sends will silently fail. Use the same environment
variables or settings file in both processes.

## 5. Attachments Now Work in Background Sends

In 1.x, `vintasend-celery` used `PlaceholderAttachmentFile` because file handles cannot be serialized
into a Celery task. Attachments now work on the background path because the worker loads the real
notification from the backend, not from a serialized payload.

No code change is needed — background adapters now send attachments just like the foreground path:

```python
# This now works for background adapters in 2.0
notification = service.create_notification(
    user_id=user_id,
    notification_type=NotificationTypes.EMAIL,
    title="Email with Attachment",
    body_template="email.html",
    context_name="my_context",
    context_kwargs={...},
    attachments=[
        NotificationAttachment(
            filename="document.pdf",
            file="/path/to/document.pdf",
            content_type="application/pdf",
        ),
    ],
)
service.send(notification)  # For background adapters, this enqueues to the worker
```

## 6. Adapter Rename and New AsyncIO Marker

In 1.x, the sync background marker was `AsyncBaseNotificationAdapter`, a confusing name (it is not
`async`/`await`; see `AsyncIOBaseNotificationAdapter` for that). The name is kept as a silent alias
in 2.0 for compatibility.

**New recommended name for sync background adapters:** `BackgroundNotificationAdapter`

**New marker for AsyncIO background adapters:** `AsyncIOBackgroundNotificationAdapter`

Imports:

```python
# Old names still work (silent aliases)
from vintasend.services.notification_adapters.async_base import AsyncBaseNotificationAdapter

# New names (recommended)
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
from vintasend.services.notification_adapters.asyncio_background_base import AsyncIOBackgroundNotificationAdapter
```

## 7. Deploy Step: Drain or Dual-Register the Queue

The Celery task function name binds to the queue at enqueue time. Tasks queued under the 1.x
`send_notification` function signature (which took many parameters) cannot be processed by the 2.0
worker (which expects only a notification id).

**Option A: Drain the queue** — run a purge before deploying the 2.0 worker so no old tasks are
waiting.

**Option B: Dual registration** — register the new 2.0 entrypoint under a new task name and run both
workers until the old queue empties, then retire the old one. This works for Celery because task
names are strings:

```python
# myapp/celery.py (2.0)
from celery import Celery
from vintasend.tasks.background_tasks import send_notification

celery_app = Celery()

# Register the 2.0 entrypoint
celery_app.task(name="send_notification", bind=False)(send_notification)

# (Optional) Keep the 1.x entrypoint on a separate name if needed for a gradual rollout
# This requires the 1.x code to still be present, which is usually not practical.
```

If you use a different queue system (SQS, Redis, Kafka, etc.), follow the same principle: ensure no
1.x-formatted tasks are waiting when the 2.0 worker starts, or register under a new name and run both
in parallel.

## 8. Context Timing Change

In 1.x, context was generated when the notification was enqueued, and a serialized copy was sent to
the worker. Scheduled notifications rendered against data from the enqueueing process.

In 2.0, context is generated in the worker at delivery time, so scheduled notifications render
against current data. The `store_context_used` field now records what the worker actually rendered,
which is more accurate.

For most applications this is invisible and beneficial. If your code relies on the stored context
reflecting the enqueueing time (rare), you may need to capture that timestamp separately before
enqueueing.

## 9. Backend Implementers: New Abstract Methods (Filtering + Attachments)

2.0 also folds in the filtering / ordering API and the attachment manager seam. Both add
`@abstractmethod`s to `BaseNotificationBackend` and `AsyncIOBaseNotificationBackend`, so **every
custom backend subclass MUST implement them on both classes or it raises `TypeError` at
instantiation.** This only affects backend maintainers; application code that consumes a shipped
backend needs no change.

### 9.1 Filtering API

- **`filter_notifications(filter, page, page_size, order_by=None)`** — the one new abstract method.
  It applies the composable filter vocabulary from
  `vintasend.services.notification_backends.filters` and returns a page of matching notifications.
- `get_filter_capabilities` (default `{}`, meaning every capability is supported) and
  `count_notifications` (default: pages through `filter_notifications` and sums the results) are
  concrete, so they need no changes but SHOULD be overridden for efficiency (for example, a database
  `COUNT` and native `WHERE` translation).
- Supporting model changes: `Notification` / `OneOffNotification` gained `sent_at`, `read_at` and
  `tenant`, and `persist_notification` / `persist_one_off_notification` gained an optional trailing
  `tenant` keyword. These are additive with `None` defaults; a backend built against the pre-2.0
  signature keeps working for tenant-less callers.

### 9.2 Attachment manager seam

Seven new abstract methods, on both backend classes:

- `store_attachment_file_record`
- `get_attachment_file_record`
- `find_attachment_file_by_checksum`
- `delete_attachment_file`
- `get_orphaned_attachment_files`
- `get_attachments`
- `delete_notification_attachment`

`inject_attachment_manager` is added too, but concrete with a default implementation, so no existing
backend needs to change for that one. Attachment *storage* itself now lives behind the
`BaseAttachmentManager` / `AsyncIOBaseAttachmentManager` seam — the backend persists records and join
rows, the manager owns the bytes. See `ATTACHMENTS.md` for the full seam and a reference manager.

### 9.3 Downstream package status

- **`vintasend-django` 2.0.0** implements the new filter and attachment seams and stores attachment
  bytes through a Django-storage-backed `DjangoAttachmentManager`. Its upgrade is covered by that
  package's own `RELEASE_NOTES.md`; the attachment-data handling is summarized in 9.4 below.
- **`vintasend-sqlalchemy`** cannot adopt 2.0 yet. It is already missing methods added in 1.2.0
  (`persist_one_off_notification`, `mark_sent_as_read_bulk`, the in-app filter methods) and needs its
  own catch-up release before it can implement the new methods here.

### 9.4 `vintasend-django` attachment data migration

The old `Attachment` model (a single row owning both the file and the notification link) is replaced
by `AttachmentFileRecord` (a checksum-indexed stored blob) plus a `NotificationAttachment` join row.
An earlier draft of this guide claimed no data migration was needed because "the old Django
attachment path never wrote a row for a real `NotificationAttachment`." **That is not safe to rely
on:** the `Attachment` table is a real table with a `FileField`, and rows can exist from the Django
admin, direct ORM use, or the pre-2.0 duck-typed `file_path` / `file_bytes` / `file_obj` write path.
Dropping it blindly would destroy those rows and orphan their files.

`vintasend-django` 2.0.0 therefore ships a **non-destructive, three-step migration** instead of a
bare `DeleteModel`:

1. `0004` — additive only: adds the new `Notification` columns (`sent_at`, `read_at`, `tenant`,
   `git_commit_sha`) and creates the two new attachment tables. Nothing is deleted.
2. `0005` — a data migration that bulk-copies every legacy `Attachment` into `AttachmentFileRecord`
   + `NotificationAttachment`. It **never reads a file** — the blob is left exactly where it is and
   the new record's `storage_identifiers` point at the same storage path — so it runs identically on
   local disk or a remote backend like S3 and cannot fail on a momentarily unreachable object. The
   trade-off is that migrated records start with an empty `checksum` and do not participate in the
   (checksum, size) dedup until re-uploaded. It is reversible.
3. `0006` — drops the now-empty `Attachment` table, only after the copy has run.

Because the migration skips file reads, a separate **opt-in** management command backfills checksums
on the operator's schedule (off-peak, in a worker, in chunks), keeping the storage-touching work out
of the deploy:

```bash
python manage.py backfill_attachment_checksums            # rows missing a checksum
python manage.py backfill_attachment_checksums --all      # recompute every record
python manage.py backfill_attachment_checksums --dry-run --limit 1000
```

It reads each file through the configured attachment manager, computes the sha256 and real size, and
is safe to re-run and to interrupt. Take a database backup before migrating, as always.

## Summary Checklist

Application maintainers:

- [ ] Restore 1.x exception-raising behavior if needed: pass `raise_on_failed_send=True` to
      `NotificationService`
- [ ] Set up `NOTIFICATION_SERVICE_FACTORY` pointing to a factory that returns a ready service (if
      you use background sending)
- [ ] Ensure web and worker read the same `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE`
      settings
- [ ] Drain or dual-register the queue before deploying the 2.0 worker
- [ ] Test background sending end-to-end, especially with attachments (now supported)

Adapter maintainers:

- [ ] Move background delivery code from `delayed_send()` into `send()`
- [ ] Delete all eight `serialize_*` / `restore_*` methods
- [ ] Move to `BackgroundNotificationAdapter` / `AsyncIOBackgroundNotificationAdapter`

Backend maintainers:

- [ ] Implement `filter_notifications` (and override `count_notifications` /
      `get_filter_capabilities` for efficiency)
- [ ] Implement the seven new attachment methods on both backend classes
- [ ] Add a schema migration for the new attachment file table
