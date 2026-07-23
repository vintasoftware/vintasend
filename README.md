# VintaSend

A flexible package for implementing transactional notifications in Python projects.

## Features
* **Storing notifications in a Database**: This package relies on a data store to record all the notifications that will be sent. It also keeps it's state column up to date.
* **Scheduling notifications**: Storing notifications to be send in the future. The notification's context for rendering the template is only evaluated at the moment the notification is sent due to the lib's context generation registry.
* **Notification context fetched at send time**: On scheduled notifications, we only get the notification context at the send time, so we always get the most up-to-date information.
* **AsyncIO Support**: We have two different versions of our service, one that only supports sync backends/adapters, and the other that only supports AsyncIO backends/adapters. 
* **File Attachments**: Support for adding file attachments to notifications with various input types including file paths, URLs, bytes data, and file-like objects.
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

## Glossary

* **Notification Backend**: It is a class that implements the methods necessary for VintaSend services to create, update, and retrieve Notifications from da database.
* **Notification Adapter**: It is a class that implements the methods necessary for VintaSend services to send Notifications through email, SMS or even push/in-app notifications.
* **Template Renderer**: It is a class that implements the methods necessary for VintaSend adapter to render the notification body.
* **Notification Context**: It's the data passed to the templates to render the notification correctly. It's generated when the notification is sent, not on creation time
* **Context generator**: It's a function registered with a name that, when called, generates the data necessary to render a notification.
* **Context name**: The registered name of a context generator. It's stored in the notification so the context generator is called at the moment the notification will be sent.
* **Context registry**: We store all registered context generators on a Singleton class, we call it context registry.
* **Notification Attachment**: Files that can be attached to notifications, supporting various input types including file paths, URLs, bytes data, and file-like objects.
* **One-off Notification**: A notification sent directly to an email address or phone number without requiring a user ID from your database. 


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
