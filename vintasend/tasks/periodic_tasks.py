from collections.abc import Iterable

from vintasend.services.notification_service import NotificationService


def periodic_send_pending_notifications(
    backend_import_str: str | None = None,
    notification_adapters: Iterable[tuple[str, str]] | None = None,
):
    notifications_service = NotificationService(
        notification_adapters,
        backend_import_str,
    )

    notifications_service.send_pending_notifications()
