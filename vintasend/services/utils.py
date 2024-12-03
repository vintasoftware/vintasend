from typing import Any


def get_class_path(cls: Any) -> str:
    return f"{cls.__class__.__module__}.{cls.__class__.__name__}"