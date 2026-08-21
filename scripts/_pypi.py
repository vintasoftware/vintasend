"""Asking PyPI what is already published.

The release runs in waves -- the root first, then the packages that depend on
it, then the packages that depend on those. What separates one wave from the
next is a version becoming installable, so every script that orders the family
needs the same question answered: is `name==version` on PyPI yet?
"""

from __future__ import annotations

import urllib.error
import urllib.request


TIMEOUT = 15

_SEEN: dict[tuple[str, str], bool | None] = {}


def on_pypi(name: str, version: str) -> bool | None:
    """Whether `name==version` is installable. None means the question failed.

    A definite "no" is worth acting on: it means a resolver would fall back to
    an older release, or fail outright. An unreachable PyPI is not -- being
    offline is not a release problem -- so callers should only warn about None.
    """
    key = (name, version)
    if key in _SEEN:
        return _SEEN[key]

    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as response:  # noqa: S310 -- literal https
            found: bool | None = response.status == 200
    except urllib.error.HTTPError as exc:
        found = False if exc.code == 404 else None
    except (urllib.error.URLError, TimeoutError, OSError):
        found = None

    _SEEN[key] = found
    return found
