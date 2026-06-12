from unittest import TestCase
from unittest.mock import MagicMock, patch

from vintasend.services.notification_adapters.stubs.fake_adapter import (
    FakeAsyncEmailAdapter,
)
from vintasend.services.notification_backends.stubs.fake_backend import FakeFileBackend
from vintasend.tasks.periodic_tasks import periodic_send_pending_notifications


RENDERER = (
    "vintasend.services.notification_template_renderers.stubs."
    "fake_templated_email_renderer.FakeTemplateRenderer"
)
SYNC_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeEmailAdapter",
    RENDERER,
)
ASYNC_ADAPTER = (
    "vintasend.services.notification_adapters.stubs.fake_adapter.FakeAsyncEmailAdapter",
    RENDERER,
)
BACKEND = "vintasend.services.notification_backends.stubs.fake_backend.FakeFileBackend"


class PeriodicSendPendingNotificationsTestCase(TestCase):
    """
    Regression tests for the adapter-deserialization logic in
    ``periodic_send_pending_notifications``.

    The buggy version only inspected ``adapter_intances[0]``: when the async
    adapter was not the *first* adapter in the list, its ``restore_*`` hooks were
    never called and the raw (serialized) kwargs/config were forwarded to the
    ``NotificationService``. The fix iterates over every adapter and uses the
    first async one it finds.
    """

    def setUp(self):
        self.backend_kwargs = {"database_file_name": "periodic-tests-notifications.json"}
        self.config = {"some": "config"}

    def tearDown(self):
        FakeFileBackend(database_file_name="periodic-tests-notifications.json").clear()

    def test_restores_kwargs_and_config_when_async_adapter_is_not_first(self):
        """Async adapter at a non-zero index must still get its restore hooks called."""
        restored_kwargs = {"restored": "backend_kwargs"}
        restored_config = {"restored": "config"}
        # MagicMock (not a descriptor) as the class attribute -> instance access
        # returns it unbound, so it is called with only the explicit argument.
        mocked_restore_kwargs = MagicMock(return_value=restored_kwargs)
        mocked_restore_config = MagicMock(return_value=restored_config)

        with (
            patch(
                "vintasend.tasks.periodic_tasks.NotificationService"
            ) as mocked_service,
            patch.object(
                FakeAsyncEmailAdapter, "restore_backend_kwargs", mocked_restore_kwargs
            ),
            patch.object(
                FakeAsyncEmailAdapter, "restore_config", mocked_restore_config
            ),
        ):
            periodic_send_pending_notifications(
                notification_adapters=[SYNC_ADAPTER, ASYNC_ADAPTER],
                backend_import_str=BACKEND,
                backend_kwargs=self.backend_kwargs,
                config=self.config,
            )

        mocked_restore_kwargs.assert_called_once_with(self.backend_kwargs)
        mocked_restore_config.assert_called_once_with(self.config)

        _, service_kwargs = mocked_service.call_args
        self.assertEqual(service_kwargs["notification_backend_kwargs"], restored_kwargs)
        self.assertEqual(service_kwargs["config"], restored_config)
        mocked_service.return_value.send_pending_notifications.assert_called_once_with()

    def test_restores_kwargs_and_config_when_async_adapter_is_first(self):
        """Sanity check: the original (working) ordering keeps working."""
        restored_kwargs = {"restored": "backend_kwargs"}
        restored_config = {"restored": "config"}

        with (
            patch(
                "vintasend.tasks.periodic_tasks.NotificationService"
            ) as mocked_service,
            patch.object(
                FakeAsyncEmailAdapter,
                "restore_backend_kwargs",
                MagicMock(return_value=restored_kwargs),
            ),
            patch.object(
                FakeAsyncEmailAdapter,
                "restore_config",
                MagicMock(return_value=restored_config),
            ),
        ):
            periodic_send_pending_notifications(
                notification_adapters=[ASYNC_ADAPTER, SYNC_ADAPTER],
                backend_import_str=BACKEND,
                backend_kwargs=self.backend_kwargs,
                config=self.config,
            )

        _, service_kwargs = mocked_service.call_args
        self.assertEqual(service_kwargs["notification_backend_kwargs"], restored_kwargs)
        self.assertEqual(service_kwargs["config"], restored_config)

    def test_falls_back_to_raw_kwargs_when_no_async_adapter(self):
        """No async adapter in the list -> forward the raw kwargs/config unchanged."""
        with patch(
            "vintasend.tasks.periodic_tasks.NotificationService"
        ) as mocked_service:
            periodic_send_pending_notifications(
                notification_adapters=[SYNC_ADAPTER],
                backend_import_str=BACKEND,
                backend_kwargs=self.backend_kwargs,
                config=self.config,
            )

        _, service_kwargs = mocked_service.call_args
        self.assertEqual(service_kwargs["notification_backend_kwargs"], self.backend_kwargs)
        self.assertEqual(service_kwargs["config"], self.config)
