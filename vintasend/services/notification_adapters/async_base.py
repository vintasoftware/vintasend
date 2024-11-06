from abc import abstractmethod

from vintasend.services.notification_adapters.base import BaseNotificationAdapter


class AsyncBaseNotificationAdapter(BaseNotificationAdapter):
    @abstractmethod
    def delayed_send(self, notification_dict: dict, context_dict: dict) -> None:
        raise NotImplementedError
