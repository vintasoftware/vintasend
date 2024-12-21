from abc import abstractmethod
from typing import Any, Generic, Protocol, TypedDict, TypeVar, runtime_checkable

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
    adapter_extra_parameters: dict | None


@runtime_checkable
class AsyncNotificationProtocol(Protocol):
    def serialize_backend_kwargs(self) -> dict: ...

    @staticmethod
    def restore_backend_kwargs(backend_kwargs: dict) -> dict: ...

    def serialize_config(self) -> dict: ...

    @staticmethod
    def restore_config(config: dict) -> Any: ...

    def serialize_adapter_kwargs(self) -> dict: ...
    
    @staticmethod
    def restore_adapter_kwargs(adapter_kwargs: dict) -> dict: ...

    def serialize_template_renderer_kwargs(self) -> dict: ...

    @staticmethod
    def restore_template_renderer_kwargs(template_renderer_kwargs: dict) -> dict: ...

    def delayed_send(self, notification_dict: NotificationDict, context_dict: dict) -> None: ...


B = TypeVar("B", bound=BaseNotificationBackend)
T = TypeVar("T", bound=BaseNotificationTemplateRenderer)


class AsyncBaseNotificationAdapter(
    Generic[B, T], AsyncNotificationProtocol, BaseNotificationAdapter[B, T]
):
    def serialize_backend_kwargs(self) -> dict:
        return self.backend.backend_kwargs

    @staticmethod
    def restore_backend_kwargs(backend_kwargs: dict[str, Any]) -> dict[str, Any]:
        return backend_kwargs

    def serialize_config(self) -> dict[str, Any]:
        return self.config

    @staticmethod
    def restore_config(config: dict[str, Any]) -> Any:
        return config
    
    def serialize_adapter_kwargs(self) -> dict:
        return self.adapter_kwargs
    
    @staticmethod
    def restore_adapter_kwargs(adapter_kwargs: dict[str, Any]) -> dict[str, Any]:
        return adapter_kwargs
    
    def serialize_template_renderer_kwargs(self) -> dict[str, Any]:
        return self.template_renderer.template_renderer_kwargs
    
    @staticmethod
    def restore_template_renderer_kwargs(template_renderer_kwargs: dict[str, Any]) -> dict[str, Any]:
        return template_renderer_kwargs

    @abstractmethod
    def delayed_send(self, notification_dict: NotificationDict, context_dict: dict) -> None:
        raise NotImplementedError
