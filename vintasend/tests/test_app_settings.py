"""Integration tests for settings resolution in `vintasend.app_settings`.

Scoped to the two settings this phase adds — `NOTIFICATION_QUEUE_SERVICE` and
`NOTIFICATION_SERVICE_FACTORY` — since none of Django, Flask, or FastAPI are dependencies of
this package and are not installed in this environment. The FastAPI path is exercised the same
way `test_notification_service.py` already does: `detect_framework` is patched directly and a
plain config object stands in for the FastAPI config, since `get_fastapi_setting` only calls
`getattr` on it and never imports `fastapi` itself. Django and Flask genuinely require the
framework to be installed to exercise their config-reading branches, so those are left untested
here per the `add-env-var` skill's guidance to guard or skip rather than add a dependency.
"""

import os
from unittest import TestCase
from unittest.mock import patch

from vintasend.app_settings import NotificationSettings
from vintasend.tests.utils import _reset_notification_settings_singleton


class _FakeFastAPIConfig:
    """Stands in for a FastAPI config object: `get_fastapi_setting` only calls `getattr`."""


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
