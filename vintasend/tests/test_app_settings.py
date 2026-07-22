"""Integration tests for `NOTIFICATION_ATTACHMENT_MANAGER` settings resolution.

`app_settings.py` detects Django, Flask, or FastAPI at runtime and none of the three is
a dependency of this package (nor installed in this test environment). Per
`Skill(add-env-var)`, only the FastAPI path is directly testable without installing a
framework -- it reads off a plain config object rather than importing `fastapi`. The
Django and Flask paths are guarded with `importlib.util.find_spec` and skipped when the
package isn't present, so they still run for real in an environment that has them.
"""

import importlib.util
import os
from types import MappingProxyType
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


_DJANGO_INSTALLED = importlib.util.find_spec("django") is not None
_FLASK_INSTALLED = importlib.util.find_spec("flask") is not None


def _reset_notification_settings_singleton(test_case: TestCase) -> None:
    """Clear the NotificationSettings singleton for one test, then restore it.

    NotificationSettings uses SingletonMeta: the first construction wins, and every
    later `config` argument is ignored. See the identical helper in
    `test_notification_service.py` for the full explanation of why clearing
    `NotificationSettings._instances` (not `SingletonMeta._instances`) is required.
    """
    sentinel = object()
    original = vars(NotificationSettings).get("_instances", sentinel)

    def _restore() -> None:
        if original is sentinel:
            if "_instances" in vars(NotificationSettings):
                delattr(NotificationSettings, "_instances")
        else:
            NotificationSettings._instances = original

    test_case.addCleanup(_restore)
    NotificationSettings._instances = MappingProxyType({})


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
