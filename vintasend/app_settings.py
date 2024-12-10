import os
from typing import Any, TypedDict, cast

from vintasend.utils.singleton_utils import SingletonMeta


class NotificationSettingsDict(TypedDict):
    NOTIFICATION_ADAPTERS: list[tuple[str, str]]
    NOTIFICATION_BACKEND: str | None
    NOTIFICATION_MODEL: str | None
    NOTIFICATION_DEFAULT_BCC_EMAILS: list[str]
    NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL: str
    NOTIFICATION_DEFAULT_BASE_URL_DOMAIN: str
    NOTIFICATION_DEFAULT_FROM_EMAIL: str 

DEFAULT_SETTINGS: NotificationSettingsDict = {
    "NOTIFICATION_ADAPTERS": [],
    "NOTIFICATION_BACKEND": None,
    "NOTIFICATION_MODEL": None,
    "NOTIFICATION_DEFAULT_BCC_EMAILS": [],
    "NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL": "http",
    "NOTIFICATION_DEFAULT_BASE_URL_DOMAIN": "example.com",
    "NOTIFICATION_DEFAULT_FROM_EMAIL": "foo@examplo.com",
}


def is_django_installed():
    try:
        import django  # type: ignore # noqa # pylint: disable=unused-import
        from django.conf import settings  # type: ignore # noqa # pylint: disable=unused-import

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


def get_setting_with_env_var_fallback(
    setting_name: str, 
    framework_value: Any | None = None, 
    default_settings: NotificationSettingsDict = DEFAULT_SETTINGS
):
    return os.getenv(
        setting_name,
        framework_value if framework_value else default_settings.get(setting_name, None)
    )


def get_django_setting(setting_name: str):
    from django.conf import settings

    DJANGO_DEFAULT_SETTINGS: NotificationSettingsDict = {
        **DEFAULT_SETTINGS,
        "NOTIFICATION_ADAPTERS": [
            (
                "vintasend_django.services.notification_adapters.django_email.DjangoEmailNotificationAdapter",
                "vintasend_django.services.notification_template_renderers.django_templated_email_renderer.DjangoTemplatedEmailRenderer",
            ),
        ],
        "NOTIFICATION_BACKEND": "vintasend_django.services.notification_backends.django_db_notification_backend.DjangoDbNotificationBackend",
        "NOTIFICATION_MODEL": "vintasend_django.models.Notification",
    }

    return get_setting_with_env_var_fallback(setting_name, getattr(settings, setting_name, None), DJANGO_DEFAULT_SETTINGS)


def get_flask_setting(setting_name: str):
    from flask import current_app  # type: ignore # noqa # pylint: disable=import-outside-toplevels

    FLASK_DEFAULT_SETTINGS: NotificationSettingsDict = {
        **DEFAULT_SETTINGS,
        "NOTIFICATION_ADAPTERS": [
            (
                "vintasend_flask_mail.services.notification_adapters.flask_mail.FlaskMailNotificationAdapter",
                "vintasend_jinja.services.notification_template_renderers.jinja_template_renderer.JinjaTemplatedEmailRenderer",
            ),
        ],
        "NOTIFICATION_BACKEND": "vintasend_sqlalchemy.services.notification_backends.sqlalchemy_notification_backend.SQLAlchemyNotificationBackend",
    }

    return get_setting_with_env_var_fallback(setting_name, current_app.config.get(setting_name, None), FLASK_DEFAULT_SETTINGS)

def get_fastapi_setting(setting_name: str, config: Any):
    FASTAPI_DEFAULT_SETTINGS: NotificationSettingsDict = {
        **DEFAULT_SETTINGS,
        "NOTIFICATION_ADAPTERS": [
            (
                "vintasend_fastapi_mail.services.notification_adapters.fastapi_mail.FastAPIMailNotificationAdapter",
                "vintasend_jinja.services.notification_template_renderers.jinja_template_renderer.JinjaTemplatedEmailRenderer",
            ),
        ],
        "NOTIFICATION_BACKEND": "vintasend_sqlalchemy.services.notification_backends.sqlalchemy_notification_backend.SQLAlchemyNotificationBackend",
    }
    return get_setting_with_env_var_fallback(setting_name, getattr(config, setting_name, None), FASTAPI_DEFAULT_SETTINGS)


def get_config(setting_name: str, config: Any = None):
    framework = detect_framework()
    if framework == "Django":
        return get_django_setting(setting_name)
    elif framework == "Flask":
        return get_flask_setting(setting_name)
    elif framework == "FastAPI":
        return get_fastapi_setting(setting_name, config)
    else:
        return {}


class NotificationSettings(metaclass=SingletonMeta):
    NOTIFICATION_ADAPTERS: list[tuple[str, str]]
    NOTIFICATION_BACKEND: str
    NOTIFICATION_MODEL: str | None
    NOTIFICATION_DEFAULT_BCC_EMAILS: list[str]
    NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL: str
    NOTIFICATION_DEFAULT_BASE_URL_DOMAIN: str
    NOTIFICATION_DEFAULT_FROM_EMAIL: str

    def __init__(self, config: Any = None):
        self.NOTIFICATION_ADAPTERS = cast(list[tuple[str, str]], get_config("NOTIFICATION_ADAPTERS", config))
        self.NOTIFICATION_BACKEND = cast(str, get_config("NOTIFICATION_BACKEND", config))
        self.NOTIFICATION_MODEL = cast(str| None, get_config("NOTIFICATION_MODEL", config))
        self.NOTIFICATION_DEFAULT_BCC_EMAILS = cast(list[str], get_config("NOTIFICATION_DEFAULT_BCC_EMAILS", config))
        self.NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL = cast(str, get_config("NOTIFICATION_DEFAULT_BASE_URL_PROTOCOL", config))
        self.NOTIFICATION_DEFAULT_BASE_URL_DOMAIN = cast(str, get_config("NOTIFICATION_DEFAULT_BASE_URL_DOMAIN", config))
        self.NOTIFICATION_DEFAULT_FROM_EMAIL = cast(str, get_config("NOTIFICATION_DEFAULT_FROM_EMAIL", config))

    def get_notification_model_cls(self):
        if self.NOTIFICATION_MODEL is None:
            raise ValueError("NOTIFICATION_MODEL is not set in the settings.")
        module_name, class_name = self.NOTIFICATION_MODEL.rsplit(".", 1)
        module = __import__(module_name, fromlist=[class_name])
        return getattr(module, class_name)
