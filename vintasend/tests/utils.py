"""Shared helpers for test modules across the `vintasend.tests` package."""

from types import MappingProxyType
from unittest import TestCase

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
