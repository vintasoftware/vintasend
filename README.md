# VintaSend

A flexible package for implementing transactional notifications in Python projects.

## Features
* **Storing notifications in a Database**: This package relies on a data store to record all the notifications that will be sent. It also keeps it's state column up to date.
* **Scheduling notifications**: Storing notifications to be send in the future. The notification's context for rendering the template is only evaluated at the moment the notification is sent due to the lib's context generation registry.
* **Notification context fetched at send time**: On scheduled notifications, we only get the notification context at the send time, so we always get the most up-to-date information.
* **AsyncIO Support**: We have two different versions of our service, one that only supports sync backends/adapters, and the other that only supports AsyncIO backends/adapters. 
* **File Attachments**: Support for adding file attachments to notifications with various input types including file paths, URLs, bytes data, and file-like objects. File storage is a pluggable seam of its own — see [Attachment storage](#attachment-storage) below and [ATTACHMENTS.md](ATTACHMENTS.md) for the full picture.
* **One-off Notifications**: Send notifications directly to email addresses or phone numbers without requiring user IDs from your database.
* **Flexible backend**: Your projects database is getting slow after you created the first milion notifications? You can migrate to a faster no-sql database with a blink of an eye without affecting how you send the notifications.
* **Flexible adapters**: Your project probably will need to change how it sends notifications overtime. This package allows to change the adapter without having to change how notifications templates are rendered or how the notification themselves are stored.
* **Flexible template renderers**: Wanna start managing your templates with a third party tool (so non-technical people can help maintaining them)? Or even choose a more powerful rendering engine? You can do it independetly of how you send the notifications or store them in the database.


## Installation

To install `vintasend` you just need to run 

```shell
pip install vintasend
```

**Disclaimer**: Although the VintaSend package is self-sufficient and can be used alone, you'd have to implement at least one Backend, one Adapter, and one Template Renderer. We have a bunch of additional packages that implement these and need to be installed seprately depending on your needs. Please check [Officially supported packages](#officially-supported-packages) section.


## Getting Started

To start using VintaSend you just need to import the notification service and start using it to manage your notifications.

```python
from vintasend.services.notification_service import NotificationService, register_context
from vintasend.services.dataclasses import NotificationAttachment
from vintasend.constants import NotificationTypes


notification_backend = MyNotificationBackend() 
notifications_service = NotificationService(
    notification_adapters=[MyAdapter(MyTemplateRenderer(), notification_backend)], 
    notification_backend=notification_backend,
)


@register_context("my_context_generator")
def my_context_generator(user_id: str):
    user = get_user_by_id(user_id)
    
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "joined_at": user.joined_at,
    }


notifications_service.create_notification(
    user_id=user.id,
    notification_type=NotificationTypes.EMAIL,
    title="My Notification", # this is just for auditing purposes
    body_template="my/notification/template/path.html",
    context_name="my_context_generator",
    context_kwargs={
        "user_id": user.id,
    },
    send_after=datetime.datetime.now(),
    subject_template="my/notification/subject/template/path.txt",
    preheader_template="my/notification/preheader/template/path.html",
)
```

### Adding Attachments to Notifications

VintaSend supports adding attachments to notifications. You can attach files using various input types:

```python
from vintasend.services.dataclasses import NotificationAttachment

# Attach a file from disk
file_attachment = NotificationAttachment(
    filename="document.pdf",
    file="/path/to/document.pdf",  # File path
    content_type="application/pdf",  # Optional, auto-detected if not provided
    description="Important document",
    is_inline=False,
)

# Attach from bytes data
bytes_attachment = NotificationAttachment(
    filename="report.txt",
    file=b"Report content here",
    content_type="text/plain",
)

# Attach from a URL
url_attachment = NotificationAttachment(
    filename="image.jpg",
    file="https://example.com/image.jpg",  # URL will be downloaded
    is_inline=True,  # For inline images in HTML emails
)

# Attach from file-like object
import io
buffer = io.BytesIO(b"File content")
buffer_attachment = NotificationAttachment(
    filename="data.bin",
    file=buffer,
)

# Create notification with attachments
notifications_service.create_notification(
    user_id=user.id,
    notification_type=NotificationTypes.EMAIL,
    title="Email with Attachments",
    body_template="notification/with/attachments.html",
    context_name="my_context_generator",
    context_kwargs={"user_id": user.id},
    attachments=[file_attachment, bytes_attachment, url_attachment],
)
```

### Attachment storage

The bytes behind an attachment are stored by an **attachment manager**, a fourth pluggable seam
alongside the backend, adapter, and template renderer. A notification backend never opens a file,
calls S3, or downloads a URL itself — it only persists a checksum-indexed file record and a join
row, and hands an opaque `storage_identifiers` value back to whichever manager is configured.

Pass a manager to the service the same way you pass a backend or an adapter, either as an instance
or a dotted import string, or configure it once through the `NOTIFICATION_ATTACHMENT_MANAGER`
setting:

```python
notifications_service = NotificationService(
    notification_adapters=[MyAdapter(MyTemplateRenderer(), notification_backend)],
    notification_backend=notification_backend,
    attachment_manager=MyAttachmentManager(),  # or a dotted import string
)
```

A backend written before this seam existed needs no changes: injection is duck-typed, so a
backend without an `inject_attachment_manager` method simply never receives one and keeps working
without attachment support.

Identical uploads are deduplicated by checksum, so attaching the same bytes twice stores one file
record and two lightweight join rows rather than two copies. You can also skip uploading entirely
and attach an already-stored file by id, with `NotificationAttachmentReference(file_id=...)`
instead of `NotificationAttachment(file=...)`.

`AsyncIONotificationService` takes the same `attachment_manager` argument and works against an
`AsyncIOBaseAttachmentManager`.

See [ATTACHMENTS.md](ATTACHMENTS.md) for the manager's ABC, the full data model
(`AttachmentFileRecord`, `StoredAttachment`, `StorageIdentifiers`), the seven methods a
notification backend implements, and the orphaned-file reclamation pattern.

### One-off Notifications

VintaSend also supports one-off notifications for sending notifications directly to email addresses or phone numbers without requiring a user ID from your database:

```python
from vintasend.services.dataclasses import NotificationAttachment

# Send a one-off notification with attachments
notifications_service.create_one_off_notification(
    email_or_phone="user@example.com",  # Email address or phone number
    first_name="John",
    last_name="Doe",
    notification_type=NotificationTypes.EMAIL,
    title="Welcome Email",
    body_template="welcome/email.html",
    context_name="welcome_context",
    context_kwargs={"welcome_message": "Welcome to our service!"},
    send_after=datetime.datetime.now(),
    subject_template="Welcome to Our Service",
    attachments=[
        NotificationAttachment(
            filename="welcome_guide.pdf",
            file="/path/to/guide.pdf",
            description="Getting started guide"
        )
    ],
)
```

This is useful for:
- Welcome emails to new signups before they're fully registered
- Marketing campaigns to external email lists
- Notifications to external parties who aren't users in your system

`email_or_phone` is validated when the notification is created. It must be either an email
address or a phone number of 10 to 15 digits, optionally prefixed with `+`. Empty, whitespace-only
and malformed values raise `InvalidOneOffNotificationRecipientError` before anything is persisted,
so a recipient that can never be delivered to is rejected up front rather than at send time. The
check is on format only -- it does not verify that the address or number actually exists.

### Scheduled notifications

VintaSend schedules notifications by creating them on the database for sending when the `send_after` value has passed. The sending isn't done automatically but we have a service method called `send_pending_notifications` to send all pending notifications found in the database.

You need to call the `send_pending_notifications` service method in a cron job or a tool like Celery Beat.

### In-app notifications (listing, counts and marking as read)

In-app notifications live in the same backend as every other notification. Once an in-app
notification has been sent it has status `SENT`; once the user has seen it, it becomes `READ`.
Internal pipeline states (`PENDING_SEND`, `FAILED`, `CANCELLED`) are never exposed by the in-app
listing methods.

All listing methods are paginated (`page` defaults to `1`, `page_size` to `10`) and pair with a
`count_*` method so you can build `count` / `next` / `previous` envelopes. All of them require an
in-app adapter to be configured, otherwise they raise `NotificationError`.

```python
# Unread only (status == SENT)
unread = notification_service.get_in_app_unread(user_id, page=1, page_size=10)
unread_count = notification_service.get_in_app_unread_count(user_id)

# Read + unread ("all", status in SENT/READ), newest-first
all_notifications = notification_service.get_in_app_notifications(user_id, page=1, page_size=10)
all_count = notification_service.get_in_app_notifications_count(user_id)
```

To mark notifications as read you can mark a single one or do it in bulk:

```python
# Single — raises NotificationUpdateError if the notification is not currently SENT
notification_service.mark_read(notification_id)

# Bulk — idempotent. ids that are already read, missing, not owned by `user_id`
# (when passed) or in a non-SENT state are simply skipped (never an error). Returns
# the final READ state for the requested ids. Always pass `user_id` for endpoints so
# you never touch another user's rows.
read_notifications = notification_service.mark_read_bulk(
    [id_1, id_2, id_3], user_id=user_id
)
```

The `AsyncIONotificationService` exposes the same methods as coroutines (`await ...`).

> Prefer the paginated `get_in_app_notifications` / `get_in_app_unread` plus the matching
> `count_*` methods. The backend's unpaginated `filter_all_*` variants exist mainly for internal
> and count use.

### Filtering and Ordering Notifications

`NotificationService` and `AsyncIONotificationService` also expose a general-purpose query --
`filter_notifications`, `count_notifications`, `get_backend_supported_filter_capabilities` and
`resend_notification` -- built for a monitoring dashboard that needs to search, page through, and
retry notifications across every user and notification type, not just one user's in-app list.

#### The filter grammar

A filter is a plain `dict` built from the `TypedDict`s in
`vintasend.services.notification_backends.filters` (`NotificationFilter` and friends), so it
round-trips through JSON with no adapter layer -- a dashboard backend can accept a filter straight
from a request body and hand it to `filter_notifications` unchanged.

**Field filters.** Multiple keys inside one filter dict are an implicit AND. A scalar value means
equality; a list means membership:

```python
from vintasend.constants import NotificationStatus

# tenant == "acme" AND status == SENT
notification_service.filter_notifications(
    {"tenant": "acme", "status": NotificationStatus.SENT.value},
    page=1,
    page_size=10,
)

# tenant in ("acme", "beta")
notification_service.filter_notifications({"tenant": ["acme", "beta"]}, page=1, page_size=10)
```

**String lookups** apply to `body_template`, `subject_template` and `context_name`. A bare `str`
is a case-sensitive `exact` match. For anything else, pass a `StringFilterLookup` dict with
`lookup` (`"exact"`, `"starts_with"`, `"ends_with"` or `"includes"`), `value`, and an optional
`case_sensitive` (defaults to `True`):

```python
notification_service.filter_notifications(
    {
        "body_template": {
            "lookup": "starts_with",
            "value": "WELCOME",
            "case_sensitive": False,
        }
    },
    page=1,
    page_size=10,
)
```

**Date ranges** apply to `send_after_range`, `created_at_range`, `sent_at_range` and
`read_at_range`. Both bounds are **inclusive** (`from` maps to `>=`, `to` maps to `<=`); a client
computing "today" as midnight-to-midnight will double-count boundary rows if it assumes an
exclusive upper bound instead. Either bound can be left out for an open-ended range:

```python
import datetime

now = datetime.datetime.now(tz=datetime.timezone.utc)

# sent in the last 24 hours
notification_service.filter_notifications(
    {"sent_at_range": {"from": now - datetime.timedelta(days=1), "to": now}},
    page=1,
    page_size=10,
)
```

**`and` / `or` / `not`** compose and nest arbitrarily -- each takes the same `NotificationFilter`
shape, so a group can hold field filters or further groups:

```python
# Everything except acme's notifications and every reminder email
notification_service.filter_notifications(
    {
        "not": {
            "or": [
                {"tenant": "acme"},
                {"body_template": "reminder_email"},
            ]
        }
    },
    page=1,
    page_size=10,
)

# acme's sent welcome emails specifically
notification_service.filter_notifications(
    {
        "and": [
            {"tenant": "acme"},
            {"status": NotificationStatus.SENT.value},
            {"body_template": {"lookup": "includes", "value": "welcome"}},
        ]
    },
    page=1,
    page_size=10,
)
```

A positive filter on a field whose stored value is `None` never matches, so a `None` row is
*included* under negation: `{"not": {"tenant": "acme"}}` returns rows whose tenant is anything but
`acme`, plus rows whose tenant is `None`.

**`filter_notifications({}, page, page_size)` -- an empty filter -- is the unrestricted listing.**
There is no separate "get everything" method; the empty dict matches every notification, not none
of them.

#### Ordering

`order_by` selects one primary field and a direction:

```python
notification_service.filter_notifications(
    {}, page=1, page_size=10, order_by={"field": "sent_at", "direction": "desc"}
)
```

The available fields are `send_after`, `sent_at`, `read_at`, `created_at` and `updated_at`.
`created_at` maps to the notification's `created` column and `updated_at` maps to `modified` --
the filter API's field names don't have to match the underlying column names one-to-one. Leaving
`order_by` out defaults to `created_at` descending. Every backend appends `id` as a tiebreaker in
the same direction as the primary field, so paging through rows that share a timestamp still
returns each row exactly once: without a stable tiebreaker, offset pagination over a repeated
`created` value would silently drop or duplicate rows across pages.

#### Pagination

`page` and `page_size` behave like every other paginated method in this library. The result is an
`Iterable`, so you can't call `len()` on it -- use `count_notifications` for the total a dashboard
needs to render `count` / `next` / `previous`:

```python
page_1 = notification_service.filter_notifications({}, page=1, page_size=20)
total = notification_service.count_notifications({})
```

There's no enforced maximum `page_size`; the library leaves that decision to the host
application. A caller that passes an unreasonably large `page_size` loads that many rows into
memory in one call, so an endpoint that exposes this to a client should apply its own cap.

#### Capability introspection

A backend doesn't have to support every filter field, string lookup and sort field.
`get_backend_supported_filter_capabilities()` returns a `dict[str, bool]` a dashboard can use to
grey out controls the configured backend can't handle:

```python
capabilities = notification_service.get_backend_supported_filter_capabilities()
capabilities["fields.tenant"]          # True unless the backend declines it
capabilities["stringLookups.startsWith"]
capabilities["orderBy.sentAt"]
```

A backend declares only what it *cannot* do. Its report is merged over an all-`True` default, so a
missing key means supported:

```python
class TenantUnsupportedBackend(FakeFileBackend):
    def get_filter_capabilities(self) -> dict[str, bool]:
        return {"fields.tenant": False}


limited_service = NotificationService(
    notification_adapters=[my_adapter],
    notification_backend=TenantUnsupportedBackend(),
)
caps = limited_service.get_backend_supported_filter_capabilities()
caps["fields.tenant"]  # False
caps["fields.status"]  # still True -- everything not declared stays supported
```

This means a filter field added in a later `vintasend` release doesn't force every existing
backend to re-declare support for everything else.

Notice that capability keys are camelCase and dotted (`'fields.notificationType'`,
`'orderBy.sentAt'`) while filter field names are snake_case (`notification_type`,
`send_after_range`). This is deliberate, not an inconsistency: filter fields are an in-process
Python API that `mypy` checks and that you type by hand, so snake_case is correct there.
Capability keys are data a client reads over the wire, and they're kept byte-identical to the
`vintasend-ts` sibling library's keys, so one dashboard can consume a capability report from a
Python backend or a TypeScript one without a translation table in between.

#### Resending a notification

`resend_notification` is the retry action a dashboard drives when a notification failed or a user
asks for it again:

```python
clone = notification_service.resend_notification(notification_id)

# Reuse the exact context the original notification rendered with, instead of
# regenerating it through the context registry
clone = notification_service.resend_notification(
    notification_id, use_stored_context_if_available=True
)
```

The source notification is left completely untouched -- its id, status and timestamps never
change. The clone is a brand-new row: same user, template, context configuration and attachments,
sent immediately (`send_after` is not carried over, since resending means "send now").
`resend_notification` raises `NotificationResendError` for a one-off notification, and for one
still scheduled in the future -- these are refusals, not silent no-ops.

The object `resend_notification` returns reflects the clone's state right after `send()` is
called on it, the same convention `create_notification` already follows -- treat it the same way.
A backend that returns a detached object from `persist_notification` may hand back something that
still looks `PENDING_SEND` even though the clone was, in fact, sent; re-fetch through
`get_notification` or `filter_notifications` if you need the backend's own freshest copy.

#### Rendering a notification from historical template content

`render_email_template_from_content` reproduces how an email notification rendered in the past,
for a preview or an audit trail -- without touching the notification's currently configured
templates, and without sending or persisting anything:

```python
from vintasend.services.notification_template_renderers.base_templated_email_renderer import (
    EmailTemplateContent,
)

notification = notification_service.get_notification(notification_id)

historical_content = EmailTemplateContent(
    subject_template="Your order {{ order_id }} has shipped",
    body_template="<p>Hi {{ first_name }}, your order is on its way.</p>",
    # preheader_template is optional -- a Python-only concept with no TS counterpart
    preheader_template="Your order is on its way",
)

templated_email = notification_service.render_email_template_from_content(
    notification,
    historical_content,
    # Render with the context the notification actually used, not a freshly generated one
    notification.context_used,
)

templated_email.subject   # "Your order 123 has shipped"
templated_email.body      # "<p>Hi Ada, your order is on its way.</p>"
templated_email.preheader # "Your order is on its way"
```

`AsyncIONotificationService.render_email_template_from_content` is the same signature, awaited.

This is a pure, read-shaped operation: it performs no I/O, generates no context (the context you
pass is used verbatim -- typically a notification's stored `context_used`), and never sends or
writes anything. It raises `NotificationRenderError` if the notification's type has no email
adapter configured, or if the configured renderer is not a `BaseTemplatedEmailRenderer`.

**Injection-safety caveat.** `template_content` is rendered as-is through the configured
renderer -- an inline template string is an injection surface if it comes from user input.
Only pass template content sourced from your own application's template history (for example, a
version you stored alongside the notification, or a git-tracked template file at a known past
revision), never a string a user typed in. Jinja2-backed renderers still apply their normal
autoescape defaults on top of this, but that's a mitigation, not a substitute for keeping the
template source trusted.

#### `tenant` is a filter field, not an access control

`tenant` behaves like `user_id` elsewhere in this library: it's an opaque string this package
doesn't interpret. The host application decides what a tenant is and who may see it.
**Filtering by `tenant` is not authorization.** Nothing stops a caller from omitting the `tenant`
filter and reading across every tenant, and nothing in this library checks whether the caller is
allowed to see the tenant it asked for. `update_notification` refuses to change a notification's
`tenant` after creation, raising `TenantReassignmentError` -- that closes off one accidental way to
move a notification between tenants, but it's defence in depth, not a security boundary. The host
application must still enforce who is allowed to query which tenant.

#### Query governance is the host application's job

Because the filter is composable, a caller can build an arbitrarily deep `and` / `or` / `not`
tree. A deeply nested `or` over an unindexed column will produce a slow scan, and this library has
no query planner to stop it. There's no built-in depth limit or leaf-filter cap -- an arbitrary
number here would be arbitrary for every host, not a real safeguard. If a filter comes from
untrusted input, the host application should validate or bound it (a maximum nesting depth, a
maximum number of leaf filters, indexes on the columns it lets clients filter by) before handing
it to `filter_notifications`.


## Background Sending via Queue Service

Background sending lets you decouple notification delivery from the web request. Adapters opt in by subclassing `BackgroundNotificationAdapter` (sync) or `AsyncIOBackgroundNotificationAdapter` (AsyncIO). When you send a notification through a background adapter, the service enqueues only the notification id and returns immediately — the worker processes the queue and actually delivers the notification.

### Configuration

Set `NOTIFICATION_QUEUE_SERVICE` to your queue service implementation:

```python
from vintasend.services.notification_service import NotificationService
from myapp.queue_services import MyQueueService

service = NotificationService(
    notification_adapters=[...],
    notification_backend=...,
    notification_queue_service=MyQueueService(),  # or as an import string
)
```

Or via environment variable:

```bash
export NOTIFICATION_QUEUE_SERVICE="myapp.queue_services.MyQueueService"
```

### Queue Service Factory

The worker needs `NOTIFICATION_SERVICE_FACTORY` to rebuild the service in its own process, since the queue payload carries only the notification id. The factory is called once per worker process and cached:

```python
# myapp/worker.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from vintasend.services.notification_service import NotificationService
from vintasend_sqlalchemy.backends import SQLAlchemyNotificationBackend

_engine = create_engine("postgresql://...")
_SessionLocal = sessionmaker(bind=_engine)

def notification_service_factory():
    """Return a notification service for this worker process."""
    session = _SessionLocal()
    backend = SQLAlchemyNotificationBackend(session)
    return NotificationService(
        notification_backend=backend,
        notification_adapters=[...],
    )
```

Then point the environment variable:

```bash
export NOTIFICATION_SERVICE_FACTORY="myapp.worker.notification_service_factory"
```

The worker and web process must read the same `NOTIFICATION_BACKEND` and `NOTIFICATION_QUEUE_SERVICE` settings.

### Worker Entrypoints

Register `send_notification` or `async_send_notification` as your queue task:

```python
# Sync task (Celery example)
from celery import Celery
from vintasend.tasks.background_tasks import send_notification

celery_app = Celery()
celery_app.task(send_notification)

# AsyncIO task
from vintasend.tasks.background_tasks import async_send_notification
celery_app.task(async_send_notification)
```

Both entrypoints take the notification id and an optional explicit service override.

### Example: Background Email

```python
from vintasend.services.notification_adapters.async_base import BackgroundNotificationAdapter
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer

class CeleryEmailAdapter(BackgroundNotificationAdapter):
    """Email adapter that uses Celery for delivery."""
    
    def send(self, notification, context):
        # Core calls this from the worker after the queue service enqueues
        email_content = self.template_renderer.render(notification, context)
        # Send email (attachments are loaded from backend)
        self._send_email(notification.email_or_phone, email_content, notification.attachments)

    def delayed_send(self, notification_id):
        # Marker only -- core never calls this. Delivery happens in send() above.
        pass
```

### Attachments in Background Sends

File attachments now work on the background path because the worker reloads the real notification from the backend, not from a serialized payload.

## Multi-Backend Configuration

A service can hold one primary backend plus zero or more **additional backends** -- extra
copies of the same data, kept in sync by replication. This is for hosts that want a hot standby,
a region-local read replica, or a bridge while migrating from one storage backend to another. A
service configured with no `additional_backends` behaves exactly like a single-backend
deployment; every read, write, and setting below is inert until you pass at least one.

### Configuring additional backends

Pass extra backend instances (or import strings) through `additional_backends`:

```python
from vintasend.services.notification_service import NotificationService

service = NotificationService(
    notification_adapters=[...],
    notification_backend=primary_backend,
    additional_backends=[replica_backend, another_replica_backend],
)
```

Each configured backend -- primary and additional -- gets a stable string identifier used to
address it later. A backend can declare its own identifier by implementing
`get_backend_identifier() -> str | None`; if it returns `None` (the concrete default every
backend gets for free), the service assigns `backend-{n}`, where `n` is the backend's position
(the primary is always `backend-0`; the first additional backend is `backend-1`, and so on).
Two configured backends that resolve to the same identifier raise
`DuplicateBackendIdentifierError` at construction -- fix a colliding custom
`get_backend_identifier()` or drop the duplicate entry.

```python
service.get_primary_backend_identifier()      # "backend-0" (or the primary's own identifier)
service.get_all_backend_identifiers()          # every identifier, primary first, in order
service.get_additional_backend_identifiers()   # every identifier except the primary's
service.has_backend("backend-1")               # True / False
```

### Read routing

Every read method (`get_notification`, `filter_notifications`, `get_future_notifications`, and
the rest of the getters) takes an optional trailing `backend_identifier` keyword:

```python
# Reads the primary (the default -- identical to a single-backend service)
service.get_notification(notification_id)

# Reads a specific replica directly
service.get_notification(notification_id, backend_identifier="backend-1")

# Unknown identifier -- raises BackendNotFoundError
service.get_notification(notification_id, backend_identifier="does-not-exist")
```

There is no fan-out read that queries every backend and merges the result -- a read always
targets exactly one backend. Comparing backends is what `verify_notification_sync` (below) is
for.

### Inline vs. queued replication

With additional backends configured, every write -- create, update, mark sent/failed/read,
cancel, store context, and so on -- replicates to them after the primary write succeeds.
`replication_mode` controls how:

* **`"inline"` (the default).** Replication happens on the request path, right after the
  primary write returns. No extra infrastructure needed; the right choice unless the added
  request latency of replicating to every backend matters.
* **`"queued"`.** The write enqueues one replication task per destination backend and returns
  immediately; a background worker (the same host-factory worker model background sending
  uses) drains the queue and applies the replication. Pick this once you already run that
  worker and want replica writes off the request path -- at the cost of replicas converging
  slightly later and of one task per additional backend per write.

```python
service = NotificationService(
    notification_adapters=[...],
    notification_backend=primary_backend,
    additional_backends=[replica_backend],
    replication_mode="queued",
    replication_queue_service=MyReplicationQueueService(),  # or an import string
)
```

Or via environment variables:

```bash
export NOTIFICATION_REPLICATION_MODE="queued"
export NOTIFICATION_REPLICATION_QUEUE_SERVICE="myapp.queue_services.MyReplicationQueueService"
```

`NOTIFICATION_REPLICATION_MODE` defaults to `"inline"`; `NOTIFICATION_REPLICATION_QUEUE_SERVICE` defaults to unset. As with every setting, an environment variable overrides the framework config value, and an instance or import string passed to the constructor overrides both.

A replication queue service implements `BaseNotificationReplicationQueueService` (sync) or `AsyncIOBaseNotificationReplicationQueueService` (AsyncIO) -- a single method, `enqueue_replication(notification_id, backend_identifier)`. A worker drains the queue by calling `service.process_replication(notification_id, backend_identifier)`, which converges that replica to the primary's current snapshot. You can also call `register_replication_queue_service(...)` to inject the queue service after construction, the same way `register_queue_service` works for background sending.

Key semantics:

* **A broken queue never silently drops replication.** If no replication queue service is configured, or the record's id cannot be resolved, queued mode logs a warning and falls back to inline replication for every backend. If enqueueing a single backend raises, only that backend is replicated inline; the ones that enqueued successfully are left to the worker.
* **Single-backend services never replicate**, regardless of `replication_mode`.
* **The worker and web process must share settings** -- the same `NOTIFICATION_BACKEND`, `NOTIFICATION_REPLICATION_QUEUE_SERVICE`, and `NOTIFICATION_SERVICE_FACTORY`, exactly as background sending requires.

### Monitoring and repair

Four methods give a dashboard everything it needs to watch replication and fix drift:

```python
# Per-field, per-backend agreement report for one notification
report = service.verify_notification_sync(notification_id)
report["in_sync"]                  # True only if every backend holds the record and every
                                    # comparable field agrees across all of them
report["backends_with_record"]     # identifiers that have the record at all
report["backends_missing_record"]  # identifiers that don't
report["fields"]                   # per-field {"field", "in_agreement", "differing_values"}

# Per-backend health: total notification count, or the error if a backend is unreachable
stats = service.get_backend_sync_stats()
stats["backend-1"]  # {"total_notifications": 1234, "status": "healthy"}

# Converge one notification to every additional backend, or a single named one
service.replicate_notification(notification_id)
service.process_replication(notification_id, target_backend_identifier="backend-1")

# Copy every notification from one backend to another, batch_size rows at a time
result = service.migrate_to_backend(
    destination_backend_identifier="backend-2",
    batch_size=500,
    source_backend_identifier=None,  # defaults to the primary
)
result["migrated"]   # count of records confirmed present on the destination
result["failures"]   # [{"notification_id": ..., "error": ...}, ...] -- doesn't abort the batch
```

`migrate_to_backend` is copy-only -- it never deletes from the source, and re-running it is
idempotent (an already-migrated record is converged, never duplicated). Retiring the source is
a separate, deliberate step you take after verifying the destination, not something this method
does for you.

### Failure semantics

Multi-backend replication is **not** synchronous multi-master, and it makes no
read-your-writes guarantee across backends. Be precise about what it actually promises:

* **The primary write commits first, and its failure is the caller's failure.** If the primary
  rejects a write, the exception propagates exactly as it would on a single-backend service.
* **Replica writes are best-effort.** A replica that rejects a write is logged, not raised --
  the primary write already succeeded and is never rolled back. A rejected replica write is
  reconciled by the next write to that record, or by calling `process_replication` /
  `verify_notification_sync` yourself.
* **No automatic failover.** A down primary is an error, full stop -- this library never
  promotes a replica to primary for you. Promoting a replica is an operational decision a human
  or an external tool makes, not something `NotificationService` does automatically.
* **Reading right after writing can see a stale replica.** A read against `backend_identifier="backend-1"` immediately after a write may not yet reflect that write, especially in `"queued"` mode. Read the primary (the default, no `backend_identifier`) when you need the current state.

## Git Commit SHA Tracking

Every notification can record which source-code revision rendered and sent it. This is opt-in: with no provider configured, nothing changes -- no SHA is ever resolved or written, and `git_commit_sha` stays `None` on every notification.

To turn it on, implement `BaseGitCommitShaProvider` (sync) or `AsyncIOBaseGitCommitShaProvider` (AsyncIO) -- a single method, `get_current_git_commit_sha() -> str | None`, that returns the commit SHA of the revision currently running:

```python
from vintasend.services.git_commit_sha_providers.base import BaseGitCommitShaProvider

class EnvVarGitCommitShaProvider(BaseGitCommitShaProvider):
    def get_current_git_commit_sha(self) -> str | None:
        return os.environ.get("GIT_COMMIT_SHA")
```

Then configure it on the service, the same way as the queue service or attachment manager -- an instance, a dotted import string, or the `NOTIFICATION_GIT_COMMIT_SHA_PROVIDER` setting:

```python
service = NotificationService(
    notification_adapters=[...],
    notification_backend=...,
    git_commit_sha_provider=EnvVarGitCommitShaProvider(),  # or as an import string
)
```

```bash
export NOTIFICATION_GIT_COMMIT_SHA_PROVIDER="myapp.git.EnvVarGitCommitShaProvider"
```

Key semantics:

* **Resolved at send time, not creation time.** The provider is called at the top of both `send()` and `delayed_send()`, so a scheduled notification records the revision that actually delivered it -- foreground or from a background worker -- not the one that enqueued it.
* **One SHA per notification**, not a history: `git_commit_sha` holds the revision that last sent it.
* **Written only when it changes.** The provider is called on every send, but the backend is only asked to persist a new value when it differs from what is already stored, so re-sending under the same revision is a no-op write.
* **Normalized and validated.** A returned SHA is trimmed, lowercased, and must match 40 hexadecimal characters; a malformed non-`None` value raises `InvalidGitCommitShaError` rather than being silently stored. A `None` return means "unknown this call" and is simply skipped.
* **Provider failures never block a send.** If the provider raises, the exception is caught and logged, and treated exactly like a `None` return -- audit metadata is never worth failing a delivery over.
* **System-managed.** `git_commit_sha` is never settable through `create_notification` or `update_notification` -- passing it to `update_notification` raises `GitCommitShaReassignmentError`. It is only ever written by the service itself, at send time.

## Glossary

* **Notification Backend**: It is a class that implements the methods necessary for VintaSend services to create, update, and retrieve Notifications from da database.
* **Notification Adapter**: It is a class that implements the methods necessary for VintaSend services to send Notifications through email, SMS or even push/in-app notifications.
* **Template Renderer**: It is a class that implements the methods necessary for VintaSend adapter to render the notification body.
* **Notification Context**: It's the data passed to the templates to render the notification correctly. It's generated when the notification is sent, not on creation time
* **Context generator**: It's a function registered with a name that, when called, generates the data necessary to render a notification.
* **Context name**: The registered name of a context generator. It's stored in the notification so the context generator is called at the moment the notification will be sent.
* **Context registry**: We store all registered context generators on a Singleton class, we call it context registry.
* **Notification Attachment**: Files that can be attached to notifications, supporting various input types including file paths, URLs, bytes data, and file-like objects.
* **Attachment Manager**: It is a class that implements the methods necessary to store, read, and delete the bytes behind a notification attachment, so the notification backend only ever handles rows, never files.
* **One-off Notification**: A notification sent directly to an email address or phone number without requiring a user ID from your database. 
* **Git Commit SHA Provider**: A class that implements a single method returning the current git commit SHA, so a notification records which source-code revision sent it.


## Community

VintaSend has many backend, adapter, and template renderer implementations. If you can't find something that fulfills your needs, the package has very clear interfaces you can implement and achieve the exact behavior you expect without loosing VintaSend's friendly API.

### Officially supported packages 

#### Backends

* **[vintasend-django](https://github.com/vintasoftware/vintasend-django/)**: Uses Django ORM to manage the notifications in the database.
* **[vintasend-sqlalchemy](https://github.com/vintasoftware/vintasend-sqlalchemy/)**: Uses SQLAlchemy to manage the notifications in the database. It supports both sync and async engines/sessions.

#### Adapters

* **[vintasend-fastapi-mail](https://github.com/vintasoftware/vintasend-fastapi-mail/)**: AsyncIO implementation that sends emails using FastAPI-Mail
* **[vintasend-flask-mail](https://github.com/vintasoftware/vintasend-flask-mail/)**: Sync implementation that sends emails using Flask-Mail.
* **[vintasend-celery](https://github.com/vintasoftware/vintasend-celery/)**: Adapter factory that allows sending emails asynchronously on sync backends by using Celery.
* **[vintasend-django](https://github.com/vintasoftware/vintasend-django/)**: Sync implementation that sends emails using Django's builtin email sender.

#### Template Renderers
* **[vintasend-django](https://github.com/vintasoftware/vintasend-django/)**: Renders emails using Django's templating system.
* **[vintasend-jinja](https://github.com/vintasoftware/vintasend-jinja/)**: Renders emails using Jinja2.

#### Attachment Managers
* **[vintasend-s3-attachments](https://github.com/vintasoftware/vintasend-s3-attachments/)**: Stores attachment files as objects in an AWS S3 bucket using boto3. Supports both sync and AsyncIO.

#### Working on them from this repo

Each officially supported package lives in its own repository and is linked here as a git
submodule under `implementations/`, so a single checkout gives you the core package plus
every implementation that has to stay compatible with it.

```bash
# Fresh clone, with the implementations
git clone --recurse-submodules git@github.com:vintasoftware/vintasend.git

# Existing clone
git submodule update --init --recursive

# Pull the latest commit of every implementation
git submodule update --remote
```

Each submodule is an ordinary checkout of its own repo: `cd` into it, branch, commit, and
push there as usual. This repo's lint, type-check, and test commands deliberately skip
`implementations/` — each package has its own dependencies, its own tooling config, and
its own CI. Committing here only records which commit of each implementation this repo
points at.

#### Creating a new implementation

If none of the packages above cover what you need, don't start from a blank directory.
`templates/vintasend-implementation-template/` is a ready-to-clone skeleton with one `TODO`
stub per seam (backend, adapter, template renderer, queue service, attachment manager) and a
matching test for each, so your clone installs and passes its test suite before you write any
real logic:

```bash
python templates/vintasend-implementation-template/scripts/clone.py /path/to/vintasend-your-integration --package-name vintasend-your-integration
```

See that package's `README.md` for the full clone-and-rename workflow and a per-component
checklist of exactly which methods to implement.


## Advanced Usage

### AsyncIO Notification Service

To use the AsyncIO Notification Service your backend and adapters must all support AsyncIO as well.

```python
from vintasend.services.notification_service import AsyncIONotificationService, register_context
from vintasend.services.dataclasses import NotificationAttachment
from vintasend.constants import NotificationTypes


notification_backend = MyAsyncIONotificationBackend() 
notifications_service = AsyncIONotificationService(
    notification_adapters=[MyAsyncIOAdapter(MyTemplateRenderer(), notification_backend)], 
    notification_backend=notification_backend,
)


@register_context("my_context_generator")
async def my_context_generator(user_id: str):
    user = await get_user_by_id(user_id)
    
    return {
        "first_name": user.first_name,
        "last_name": user.last_name,
        "joined_at": user.joined_at,
    }


await notifications_service.create_notification(
    user_id=user.id,
    notification_type=NotificationTypes.EMAIL,
    title="My Notification", # this is just for auditing purposes
    body_template="my/notification/template/path.html",
    context_name="my_context_generator",
    context_kwargs={
        "user_id": user.id,
    },
    send_after=datetime.datetime.now(),
    subject_template="my/notification/subject/template/path.txt",
    preheader_template="my/notification/preheader/template/path.html",
)
```

**Note**: Attachments work the same way with `AsyncIONotificationService` - just pass the `attachments` parameter with a list of `NotificationAttachment` objects.

**Background sending** also works with `AsyncIONotificationService`. Subclass `AsyncIOBackgroundNotificationAdapter` and pass an `AsyncIOBaseNotificationQueueService` to send via a queue:

```python
from vintasend.services.notification_service import AsyncIONotificationService
from vintasend.services.notification_adapters.asyncio_background_base import AsyncIOBackgroundNotificationAdapter

class MyAsyncIOBackgroundAdapter(AsyncIOBackgroundNotificationAdapter):
    async def send(self, notification, context):
        # Worker calls this to deliver
        email_content = self.template_renderer.render(notification, context)
        await self._send_email(notification, email_content)

    async def delayed_send(self, notification_id):
        # Marker only -- core never calls this. Delivery happens in send() above.
        pass

notifications_service = AsyncIONotificationService(
    notification_adapters=[MyAsyncIOBackgroundAdapter(...)],
    notification_backend=...,
    notification_queue_service=...,  # AsyncIO queue service
)
```

### Using frameworks that don't use a globally-available configuration

Frameworks like FastAPI don't have a centralized configuration object that's globally accessible by default. Because of that, we need to manually pass the configuration object to the service in order to initialize VintaSend configuration.


```python
from vintasend.services.notification_service import AsyncIONotificationService, register_context
from vintasend.services.dataclasses import NotificationAttachment
from vintasend.constants import NotificationTypes
from fastapi import FastAPI
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Awesome API"
    admin_email: str
    items_per_user: int = 50

settings = Settings()
app = FastAPI()

notification_backend = MyAsyncIONotificationBackend() 
notifications_service = AsyncIONotificationService(
    notification_adapters=[MyAsyncIOAdapter(MyTemplateRenderer(), notification_backend)], 
    notification_backend=notification_backend,
    config=settings,
)
```
