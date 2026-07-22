# Migration to VintaSend 2.0.0

VintaSend 2.0 brings background sending with a queue service, id-only queue payloads, and context generation in the worker. This is a major release with breaking changes that improve reliability and simplify the architecture. This guide covers the six breaking changes and the deploy procedure.

## 1. Silent Behavior Change: `raise_on_failed_send` Defaults to `False`

**This is the most important migration point.** In 1.x, when a notification fails to send, `NotificationSendError` was raised. In 2.0, it is logged but not raised by default — notification failures no longer propagate to the caller.

If your application catches `NotificationSendError` or similar exceptions to log, alert, or handle failures:

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

If you do not catch these exceptions, no code change is needed — the notification is logged either way.

## 2. Service-Level `delayed_send()` Signature Change

In 1.x, adapters declared background support by subclassing `AsyncBaseNotificationAdapter` and implementing `delayed_send(notification_dict, context_dict)`. That method was never called by core — it existed only as a marker — and serializing the notification and context into the payload meant attachments could not work in background sends.

In 2.0, the adapter's marker method is still there (now `delayed_send(notification_id)`, taking only an id) but core never calls it. Instead, the service-level `delayed_send(notification_id)` loads the notification, generates context in the worker, and calls the adapter's `send()` — the same method the foreground path uses.

**If you maintain an adapter** that subclasses `AsyncBaseNotificationAdapter` (now `BackgroundNotificationAdapter`):

```python
# 1.x adapter
class MyBackgroundAdapter(AsyncBaseNotificationAdapter):
    def send(self, notification, context):
        # Foreground delivery
        pass
    
    def delayed_send(self, notification_dict, context_dict):
        # 1.x: background delivery code lived here
        # This was never called by core; it was only a marker
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

In 1.x, the queue payload carried the entire serialized notification so the worker could rebuild it without accessing the database. This required eight abstract methods on `AsyncNotificationProtocol`: `serialize_backend_config`, `restore_backend_config`, `serialize_backend_kwargs`, `restore_backend_kwargs`, `serialize_adapter_kwargs`, `restore_adapter_kwargs`, `serialize_adapter_extra_parameters`, `restore_adapter_extra_parameters`. These and the types they used — `NotificationDict` and `OneOffNotificationDict` — are deleted in 2.0.

With the id-only payload, the queue carries nothing but a number; the worker reloads the notification from the backend. No serialization is needed.

**If you implemented these methods** in an adapter subclass, delete them:

```python
# 1.x: these methods existed
class MyAdapter(AsyncBaseNotificationAdapter):
    def serialize_backend_config(self, config):
        return json.dumps(config)
    
    def restore_backend_config(self, serialized):
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

In 1.x, a background adapter enqueued a serialized notification, and the worker reconstructed it from the payload. In 2.0, the worker needs to rebuild the service from scratch to access a live database session or connection pool.

If your application uses background sending (any adapter subclasses `BackgroundNotificationAdapter` or `AsyncIOBackgroundNotificationAdapter`), point `NOTIFICATION_SERVICE_FACTORY` to a callable that returns a ready `NotificationService` or `AsyncIONotificationService`.

The factory is called once per worker process and the result is cached, so it must be safe to call once per process and the service must be safe to reuse across tasks.

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

For FastAPI or other frameworks without a global settings module, pass `config` to your service or set the env var only.

**The worker and web process must read the same `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE` settings.** A worker pointed at a different backend will not find the notification the id refers to, and background sends will silently fail. Use the same environment variables or settings file in both processes.

## 5. Attachments Now Work in Background Sends

In 1.x, `vintasend-celery` used `PlaceholderAttachmentFile` because file handles cannot be serialized into a Celery task. Attachments now work on the background path because the worker loads the real notification from the backend, not from a serialized payload.

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

In 1.x, the sync background marker was `AsyncBaseNotificationAdapter`, a confusing name (it is not `async`/`await`; see `AsyncIOBaseNotificationAdapter` for that). The name is kept as a silent alias in 2.0 for compatibility.

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

The Celery task function name binds to the queue at enqueue time. Tasks queued under the 1.x `send_notification` function signature (which took many parameters) cannot be processed by the 2.0 worker (which expects only a notification id).

**Option A: Drain the queue** — run a purge before deploying the 2.0 worker so no old tasks are waiting.

**Option B: Dual registration** — register the new 2.0 entrypoint under a new task name and run both workers until the old queue empties, then retire the old one. This works for Celery because task names are strings:

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

If you use a different queue system (SQS, Redis, Kafka, etc.), follow the same principle: ensure no 1.x-formatted tasks are waiting when the 2.0 worker starts, or register under a new name and run both in parallel.

## 8. Context Timing Change

In 1.x, context was generated when the notification was enqueued, and a serialized copy was sent to the worker. Scheduled notifications rendered against data from the enqueueing process.

In 2.0, context is generated in the worker at delivery time, so scheduled notifications render against current data. The `store_context_used` field now records what the worker actually rendered, which is strictly more accurate.

For most applications this is invisible and beneficial. If your code relies on the stored context reflecting the enqueueing time (rare), you may need to capture that timestamp separately before enqueueing.

## Summary Checklist

- [ ] Restore 1.x exception-raising behavior if needed: pass `raise_on_failed_send=True` to `NotificationService`
- [ ] Adapter authors: move background delivery code from `delayed_send()` into `send()`
- [ ] Adapter authors: delete all eight `serialize_*` / `restore_*` methods
- [ ] Set up `NOTIFICATION_SERVICE_FACTORY` pointing to a factory that returns a ready service
- [ ] Ensure web and worker read the same `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE` settings
- [ ] Drain or dual-register the queue before deploying the 2.0 worker
- [ ] Test background sending end-to-end, especially with attachments (now supported)
