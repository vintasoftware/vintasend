from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast


class BaseSingletonMeta(type):
    _instances: MappingProxyType[Any, Any]


T = TypeVar('T', bound=BaseSingletonMeta)


class SingletonMeta(Generic[T], BaseSingletonMeta):
    _instances: MappingProxyType["SingletonMeta[T]", T] = MappingProxyType({})

    def __call__(cls, *args: Any, **kwargs: Any) -> T:
        if cls not in cls._instances:
            _instances: dict["SingletonMeta[T]", T] = dict(cls._instances)
            _instances[cls] = cast(T, super().__call__(*args, **kwargs))
            cls._instances = MappingProxyType(_instances)
        return cls._instances[cls]
