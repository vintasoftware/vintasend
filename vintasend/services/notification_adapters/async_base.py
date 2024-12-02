from abc import abstractmethod
from typing import Protocol, TypedDict, runtime_checkable

from vintasend.services.notification_adapters.base import BaseNotificationAdapter


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


class AsyncBaseNotificationAdapter(AsyncNotificationProtocol, BaseNotificationAdapter):
    @abstractmethod
    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
        raise NotImplementedError
