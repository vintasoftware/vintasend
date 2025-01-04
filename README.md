# VintaSend

A flexible package for implementing transactional notifications in Python projects.

## Features
* **Storing notifications in a Database**: This package relies on a data store to record all the notifications that will be sent. It also keeps it's state column up to date.
* **Scheduling notifications**: Storing notifications to be send in the future. The notification's context for rendering the template is only evaluated at the moment the notification is sent due to the lib's context generation registry.
* **Notification context fetched at send time**: On scheduled notifications, we only get the notification context at the send time, so we always get the most up-to-date information.
* **AsyncIO Support**: We have two different versions of our service, one that only supports sync backends/adapters, and the other that only supports AsyncIO backends/adapters. 
* **Flexible backend**: Your projects database is getting slow after you created the first milion notifications? You can migrate to a faster no-sql database with a blink of an eye without affecting how you send the notifications.
* **Flexible adapters**: Your project probably will need to change how it sends notifications overtime. This package allows to change the adapter without having to change how notifications templates are rendered or how the notification themselves are stored.
* **Flexible template renderers**: Wanna start managing your templates with a third party tool (so non-technical people can help maintaining them)? Or even choose a more powerful rendering engine? You can do it independetly of how you send the notifications or store them in the database.


## Installation

To install `vintasend` you just need to run 

```shell
pip install vintasend
```

**Disclaimer**: Although the VintaSend package is selfsufficient and can be used alone, you'd have to implement at lease one Backend, one Adapter, and one Template Renderer. We have a bunch of additional packages that implement these and need to be installed seprately depending on your needs. Please check [Officially supported packages](#officially-supported-packages) section.


## Getting Started

To start using VintaSend you just need to import the notification service and start using it to manage your notifications.

```python
from vintasend.services.notification_service import NotificationService, register_context
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

### Scheduled notifications

VintaSend schedules notifications by creating them on the database for sending when the `send_after` value has passed. The sending isn't done automatically but we have a service method called `send_pending_notifications` to send all pending notifications found in the database.

You need to call the `send_pending_notifications` service method in a cron job or a tool like Celery Beat.


## Glossary

* **Notification Backend**: It is a class that implements the methods necessary for VintaSend services to create, update, and retrieve Notifications from da database.
* **Notification Adapter**: It is a class that implements the methods necessary for VintaSend services to send Notifications through email, SMS or even push/in-app notifications.
* **Template Renderer**: It is a class that implements the methods necessary for VintaSend adapter to render the notification body.
* **Notification Context**: It's the data passed to the templates to render the notification correctly. It's generated when the notification is sent, not on creation time
* **Context generator**: It's a function registered with a name that, when called, generates the data necessary to render a notification.
* **Context name**: The registered name of a context generator. It's stored in the notification so the context generator is called at the moment the notification will be sent.
* **Context registry**: We store all registered context generators on a Singleton class, we call it context registry. 


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


## Advacted Usage

### AsyncIO Notification Service

To use the AsyncIO Notification Service your backend and adapters must all support AsyncIO as well.

```python
from vintasend.services.notification_service import AsyncIONotificationService, register_context
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

### Using frameworks that don't use a globally-available configuration

Frameworks like FastAPI don't have a centralized configuration object that's globally accessible by default. Because of that, we need to manually pass the configuration object to the service in order to initialize VintaSend configuration.


```python
from vintasend.services.notification_service import AsyncIONotificationService, register_context
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
