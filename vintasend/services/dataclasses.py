import datetime
import uuid
from dataclasses import dataclass
from typing import TypedDict


class NotificationContextDict(dict):
    """
    A dictionary that only accepts string keys and values of types: int, float, str,
    list[NotificationContextDict], and dict[str, NotificationContextDict].
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.update(*args, **kwargs)

    def update(self, *args, **kwargs):
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

    def __setitem__(
        self,
        key: str,
        value: int
        | float
        | str
        | list["NotificationContextDict"]
        | dict[str, "NotificationContextDict"],
    ):
        if not isinstance(key, str):
            raise TypeError("Keys must be strings")
        if not isinstance(
            value,
            (int | float | str | list | dict),
        ):
            raise TypeError("Value must be an int, float, str, list, or dict")
        if isinstance(value, list):
            value = [self._validate_list_item(item) for item in value]
        if isinstance(value, dict):
            value = {k: self._validate_dict_value(v) for k, v in value.items()}
        super().__setitem__(key, value)

    def _validate_list_item(self, item):
        if not isinstance(item, NotificationContextDict):
            raise TypeError("List items must be SerializableDict instances")
        return item

    def _validate_dict_value(self, value):
        if not isinstance(value, NotificationContextDict):
            raise TypeError("Dict values must be SerializableDict instances")
        return value

    def copy(self) -> "NotificationContextDict":
        return self.__class__(super().copy())


@dataclass
class Notification:
    id: int | str | uuid.UUID  # noqa: A003
    user_id: int | str | uuid.UUID
    notification_type: str
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str
    preheader_template: str
    status: str
    context_used: dict | None = None
    adapter_used: str | None = None
    adapter_extra_parameters: dict | None = None


class UpdateNotificationKwargs(TypedDict, total=False):
    title: str
    body_template: str
    context_name: str
    context_kwargs: dict[str, int | str | uuid.UUID]
    send_after: datetime.datetime | None
    subject_template: str | None
    preheader_template: str | None
    adapter_extra_parameters: dict | None
