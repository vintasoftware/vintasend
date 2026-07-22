from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vintasend.services.notification_template_renderers.base import (
    BaseNotificationTemplateRenderer,
    NotificationSendInput,
)


if TYPE_CHECKING:
    from vintasend.services.dataclasses import (
        Notification,
        NotificationContextDict,
        OneOffNotification,
    )


@dataclass
class TemplatedEmail(NotificationSendInput):
    subject: str
    body: str


class BaseTemplatedEmailRenderer(BaseNotificationTemplateRenderer):
    @abstractmethod
    def render(
        self,
        notification: "Notification | OneOffNotification",
        context: "NotificationContextDict",
        **kwargs,
    ) -> TemplatedEmail:
        raise NotImplementedError
