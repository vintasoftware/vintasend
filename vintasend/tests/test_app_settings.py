import importlib.util
import os
from unittest import TestCase
from unittest.mock import patch

import pytest

from vintasend.app_settings import (
    DEFAULT_SETTINGS,
    DJANGO_DEFAULT_SETTINGS,
    FASTAPI_DEFAULT_SETTINGS,
    FLASK_DEFAULT_SETTINGS,
    NotificationSettings,
    NotificationSettingsDict,
)
from vintasend.services.helpers import get_attachment_manager
from vintasend.tests.utils import _reset_notification_settings_singleton


_DJANGO_INSTALLED = importlib.util.find_spec("django") is not None
_FLASK_INSTALLED = importlib.util.find_spec("flask") is not None


class _FakeFastAPIConfig:
    """Stands in for a FastAPI config object: `get_fastapi_setting` only calls `getattr`."""


class NotificationSettingsDictTestCase(TestCase):
    """Static checks that don't require framework detection at all."""

    def test_new_setting_key_is_declared_on_the_typed_dict(self):
        assert "NOTIFICATION_ATTACHMENT_MANAGER" in NotificationSettingsDict.__annotations__

    def test_default_settings_has_no_attachment_manager(self):
        assert DEFAULT_SETTINGS["NOTIFICATION_ATTACHMENT_MANAGER"] is None

    def test_framework_defaults_do_not_override_the_shared_default(self):
        # No manager ships in core, and the correct default doesn't differ per
        # framework, so none of the three dicts should override DEFAULT_SETTINGS here.
        assert DJANGO_DEFAULT_SETTINGS["NOTIFICATION_ATTACHMENT_MANAGER"] is None
        assert FLASK_DEFAULT_SETTINGS["NOTIFICATION_ATTACHMENT_MANAGER"] is None
        assert FASTAPI_DEFAULT_SETTINGS["NOTIFICATION_ATTACHMENT_MANAGER"] is None


class FastApiAttachmentManagerSettingTestCase(TestCase):
    """FastAPI has no global config object, so `get_fastapi_setting` only reads
    attributes off whatever `config` the caller passes -- no `import fastapi` is
    needed, which is what makes this path testable without the dependency installed.
    """

    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_default_is_none_when_nothing_is_set(self):
        class _FakeConfig:
            pass

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, clear=False):
                os.environ.pop("NOTIFICATION_ATTACHMENT_MANAGER", None)
                settings = NotificationSettings(_FakeConfig())

        assert settings.NOTIFICATION_ATTACHMENT_MANAGER is None

    def test_framework_config_value_is_used_when_env_var_is_absent(self):
        class _FakeConfig:
            NOTIFICATION_ATTACHMENT_MANAGER = "my_app.attachments.MyAttachmentManager"

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, clear=False):
                os.environ.pop("NOTIFICATION_ATTACHMENT_MANAGER", None)
                settings = NotificationSettings(_FakeConfig())

        assert settings.NOTIFICATION_ATTACHMENT_MANAGER == "my_app.attachments.MyAttachmentManager"

    def test_env_var_overrides_the_framework_config_value(self):
        class _FakeConfig:
            NOTIFICATION_ATTACHMENT_MANAGER = "my_app.attachments.MyAttachmentManager"

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(os.environ, {"NOTIFICATION_ATTACHMENT_MANAGER": "from_env.EnvManager"}):
                settings = NotificationSettings(_FakeConfig())

        assert settings.NOTIFICATION_ATTACHMENT_MANAGER == "from_env.EnvManager"


class UnknownFrameworkAttachmentManagerTestCase(TestCase):
    """Bare-env (no Django/Flask/FastAPI detected) is a supported deployment mode --
    and what this test suite itself runs under. `get_config` returns `{}` for this
    path rather than `None`, so `get_attachment_manager` must treat any falsy value
    (`None`, `{}`, or `""`) as "unset" and return `None` rather than crashing.
    """

    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_get_attachment_manager_returns_none_without_raising(self):
        with patch("vintasend.app_settings.detect_framework", return_value="Unknown"):
            with patch.dict(os.environ, clear=False):
                os.environ.pop("NOTIFICATION_ATTACHMENT_MANAGER", None)
                result = get_attachment_manager(None)

        assert result is None


@pytest.mark.skipif(not _DJANGO_INSTALLED, reason="Django is not installed")
class DjangoAttachmentManagerSettingTestCase(TestCase):
    """Only runs when Django happens to be installed -- it isn't a dependency of this
    package, so this exercises the real `django.conf.settings` path when available and
    is skipped otherwise, per `Skill(add-env-var)`.
    """

    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_django_setting_value_is_used_when_env_var_is_absent(self):
        from django.conf import settings as django_settings

        with patch("vintasend.app_settings.detect_framework", return_value="Django"):
            with patch.object(
                django_settings,
                "NOTIFICATION_ATTACHMENT_MANAGER",
                "my_app.attachments.MyAttachmentManager",
                create=True,
            ):
                with patch.dict(os.environ, clear=False):
                    os.environ.pop("NOTIFICATION_ATTACHMENT_MANAGER", None)
                    settings = NotificationSettings()

        assert settings.NOTIFICATION_ATTACHMENT_MANAGER == "my_app.attachments.MyAttachmentManager"


@pytest.mark.skipif(not _FLASK_INSTALLED, reason="Flask is not installed")
class FlaskAttachmentManagerSettingTestCase(TestCase):
    """Only runs when Flask happens to be installed -- it isn't a dependency of this
    package, so this exercises the real `flask.current_app.config` path when available
    and is skipped otherwise, per `Skill(add-env-var)`.
    """

    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_flask_config_value_is_used_when_env_var_is_absent(self):
        import flask

        app = flask.Flask(__name__)
        app.config["NOTIFICATION_ATTACHMENT_MANAGER"] = "my_app.attachments.MyAttachmentManager"

        with app.app_context():
            with patch("vintasend.app_settings.detect_framework", return_value="Flask"):
                with patch.dict(os.environ, clear=False):
                    os.environ.pop("NOTIFICATION_ATTACHMENT_MANAGER", None)
                    settings = NotificationSettings()

        assert settings.NOTIFICATION_ATTACHMENT_MANAGER == "my_app.attachments.MyAttachmentManager"


class NotificationQueueServiceSettingTestCase(TestCase):
    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_unset_reads_as_empty_dict_when_no_framework_is_detected(self):
        """`get_config` returns `{}`, not `None`, when no framework is detected."""
        with patch("vintasend.app_settings.detect_framework", return_value="Unknown"):
            settings = NotificationSettings()

        assert settings.NOTIFICATION_QUEUE_SERVICE == {}

    def test_resolves_from_framework_config(self):
        config = _FakeFastAPIConfig()
        config.NOTIFICATION_QUEUE_SERVICE = "myapp.queue.MyQueueService"  # type: ignore[attr-defined]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            settings = NotificationSettings(config)

        assert settings.NOTIFICATION_QUEUE_SERVICE == "myapp.queue.MyQueueService"

    def test_resolves_from_env_var_when_framework_config_is_unset(self):
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(
                os.environ, {"NOTIFICATION_QUEUE_SERVICE": "env.queue.EnvQueueService"}
            ):
                settings = NotificationSettings(_FakeFastAPIConfig())

        assert settings.NOTIFICATION_QUEUE_SERVICE == "env.queue.EnvQueueService"

    def test_env_var_wins_over_framework_config(self):
        config = _FakeFastAPIConfig()
        config.NOTIFICATION_QUEUE_SERVICE = "myapp.queue.MyQueueService"  # type: ignore[attr-defined]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(
                os.environ, {"NOTIFICATION_QUEUE_SERVICE": "env.queue.EnvQueueService"}
            ):
                settings = NotificationSettings(config)

        assert settings.NOTIFICATION_QUEUE_SERVICE == "env.queue.EnvQueueService"

    def test_defaults_to_none_when_framework_detected_but_setting_is_unset(self):
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            settings = NotificationSettings(_FakeFastAPIConfig())

        assert settings.NOTIFICATION_QUEUE_SERVICE is None


class NotificationServiceFactorySettingTestCase(TestCase):
    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_unset_reads_as_empty_dict_when_no_framework_is_detected(self):
        """`get_config` returns `{}`, not `None`, when no framework is detected."""
        with patch("vintasend.app_settings.detect_framework", return_value="Unknown"):
            settings = NotificationSettings()

        assert settings.NOTIFICATION_SERVICE_FACTORY == {}

    def test_resolves_from_framework_config(self):
        config = _FakeFastAPIConfig()
        config.NOTIFICATION_SERVICE_FACTORY = "myapp.worker.build_service"  # type: ignore[attr-defined]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            settings = NotificationSettings(config)

        assert settings.NOTIFICATION_SERVICE_FACTORY == "myapp.worker.build_service"

    def test_resolves_from_env_var_when_framework_config_is_unset(self):
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(
                os.environ, {"NOTIFICATION_SERVICE_FACTORY": "env.worker.build_service"}
            ):
                settings = NotificationSettings(_FakeFastAPIConfig())

        assert settings.NOTIFICATION_SERVICE_FACTORY == "env.worker.build_service"

    def test_env_var_wins_over_framework_config(self):
        config = _FakeFastAPIConfig()
        config.NOTIFICATION_SERVICE_FACTORY = "myapp.worker.build_service"  # type: ignore[attr-defined]

        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            with patch.dict(
                os.environ, {"NOTIFICATION_SERVICE_FACTORY": "env.worker.build_service"}
            ):
                settings = NotificationSettings(config)

        assert settings.NOTIFICATION_SERVICE_FACTORY == "env.worker.build_service"

    def test_defaults_to_none_when_framework_detected_but_setting_is_unset(self):
        with patch("vintasend.app_settings.detect_framework", return_value="FastAPI"):
            settings = NotificationSettings(_FakeFastAPIConfig())

        assert settings.NOTIFICATION_SERVICE_FACTORY is None
