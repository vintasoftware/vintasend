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
from types import MappingProxyType
from unittest import TestCase
from unittest.mock import patch

from vintasend.app_settings import NotificationSettings


def _reset_notification_settings_singleton(test_case: TestCase) -> None:
    """Clear the NotificationSettings singleton for one test, then restore it.

    NotificationSettings uses SingletonMeta: the first construction wins, and every later
    `config` argument is ignored. SingletonMeta.__call__ stores the built instance on an
    `_instances` attribute it sets directly on the class being constructed. Once
    NotificationSettings has been built once, that per-class attribute shadows the empty
    default living on SingletonMeta itself, so clearing SingletonMeta's own `_instances` has
    no effect at that point. NotificationSettings's own `_instances` attribute is the one
    that must be cleared, and it must be restored after the test. This state is process-global,
    so leaking it would make other tests order-dependent.
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


class _FakeFastAPIConfig:
    """Stands in for a FastAPI config object: `get_fastapi_setting` only calls `getattr`."""


class NotificationQueueServiceSettingTestCase(TestCase):
    def setUp(self):
        _reset_notification_settings_singleton(self)

    def test_unset_reads_as_empty_dict_when_no_framework_is_detected(self):
        """`get_config` returns `{}`, not `None`, when no framework is detected.

        Django, Flask, and FastAPI are all absent from this environment, so
        `detect_framework()` genuinely resolves to "Unknown" without needing to be patched.
        """
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
