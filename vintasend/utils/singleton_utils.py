from types import MappingProxyType
from typing import Any


class SingletonMeta(type):
    _instances: MappingProxyType[str, Any] = MappingProxyType({})

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances = dict(cls._instances)
            cls._instances[cls] = super().__call__(*args, **kwargs)
            cls._instances = MappingProxyType(cls._instances)
        return cls._instances[cls]
