from abc import abstractmethod
from typing import Protocol, TypedDict, runtime_checkable, TypeVar, Generic

from vintasend.services.notification_adapters.base import BaseNotificationAdapter
from vintasend.services.notification_backends.base import BaseNotificationBackend
from vintasend.services.notification_template_renderers.base import BaseNotificationTemplateRenderer


class NotificationDict(TypedDict):
    id: int | str  # noqa: A003
    user_id: int | str
    notification_type: str
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str]
    send_after: str | None
    subject_template: str
    preheader_template: str
    status: str
    context_used: dict | None


@runtime_checkable
class AsyncNotificationProtocol(Protocol):
    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
        ...


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class AsyncBaseNotificationAdapter(Generic[B, T], AsyncNotificationProtocol, BaseNotificationAdapter[B, T]):
    @abstractmethod
    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
        raise NotImplementedError
