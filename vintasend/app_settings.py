import os
from typing import Any


DEFAULT_SETTINGS = {
    "NOTIFICATION_ADAPTERS": [
        (
            "vintasend_django.services.notification_adapters.django_email.DjangoEmailNotificationAdapter",
            "vintasend_django.services.notification_template_renderers.django_templated_email_renderer.DjangoTemplatedEmailRenderer",
        ),
    ],
    "NOTIFICATION_BACKEND": "vintasend_django.services.notification_backends.django_db_notification_backend.DjangoDbNotificationBackend",
    "NOTIFICATION_MODEL": "vintasend_django.models.Notification",
}


def is_django_installed():
    try:
        import django  # type: ignore # noqa # pylint: disable=unused-import
        from django.conf import settings

        return True if settings.configured else False
    except ImportError:
        return False


def is_flask_installed():
    try:
        import flask  # type: ignore # noqa # pylint: disable=unused-import

        return True if flask.current_app else False
    except (ImportError, RuntimeError):
        return False


def is_fastapi_installed():
    try:
        import fastapi  # type: ignore # noqa # pylint: disable=unused-import

        return True
    except ImportError:
        return False


def detect_framework():
    if is_django_installed():
        return "Django"
    elif is_flask_installed():
        return "Flask"
    elif is_fastapi_installed():
        return "FastAPI"
    else:
        return "Unknown"


def get_setting_with_env_var_fallback(setting_name: str, framework_value: Any | None = None):
    return os.getenv(
        setting_name,
        framework_value if framework_value else DEFAULT_SETTINGS.get(setting_name, None),
    )


def get_django_setting(setting_name: str):
    from django.conf import settings

    return get_setting_with_env_var_fallback(setting_name, getattr(settings, setting_name, None))


def get_flask_setting(setting_name: str):
    from flask import current_app

    return current_app.config.get(setting_name, DEFAULT_SETTINGS.get(setting_name, None))


def get_fastapi_setting(setting_name: str):
    from fastapi import FastAPI

    return FastAPI().state.settings.get(setting_name, DEFAULT_SETTINGS.get(setting_name, None))


def get_config(setting_name: str):
    if detect_framework() == "Django":
        return get_django_setting(setting_name)
    elif detect_framework() == "Flask":
        return get_flask_setting(setting_name)
    elif detect_framework() == "FastAPI":
        return get_fastapi_setting(setting_name)
    else:
        return {}


class NotificationSettings:
    NOTIFICATION_ADAPTERS: list[tuple[str, str]]
    NOTIFICATION_BACKEND: str
    NOTIFICATION_MODEL: str | None

    def __init__(self):
        self.NOTIFICATION_ADAPTERS = get_config("NOTIFICATION_ADAPTERS")
        self.NOTIFICATION_BACKEND = get_config("NOTIFICATION_BACKEND")
        self.NOTIFICATION_MODEL = get_config("NOTIFICATION_MODEL")

    def get_notification_model_cls(self):
        module_name, class_name = self.NOTIFICATION_MODEL.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
