"""Asking PyPI what is already published.

The release runs in waves -- the root first, then the packages that depend on
it, then the packages that depend on those. What separates one wave from the
next is a version becoming installable, so every script that orders the family
needs the same question answered: is `name==version` on PyPI yet?
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable


TIMEOUT = 15

_SEEN: dict[tuple[str, str], bool | None] = {}


def on_pypi(name: str, version: str, refresh: bool = False) -> bool | None:
    """Whether `name==version` is installable. None means the question failed.

    A definite "no" is worth acting on: it means a resolver would fall back to
    an older release, or fail outright. An unreachable PyPI is not -- being
    offline is not a release problem -- so callers should only warn about None.

    Answers are remembered, since a release run asks the same question about the
    same version many times. `refresh` skips that, which is what a caller
    waiting for a publish to land needs -- there, the answer changing is the
    whole point.
    """
    key = (name, version)
    if key in _SEEN and not refresh:
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


def wait_for_pypi(
    names: list[str],
    version: str,
    timeout: float,
    interval: float,
    log: Callable[[str], None] = print,
) -> list[str]:
    """Block until every `name==version` is installable. Returns what never showed.

    Pushing a tag only starts a build: each package runs its test matrix before
    uploading, so the gap between the push and the version being installable is
    minutes at best. The next wave cannot be locked until it closes.
    """
    pending = list(names)
    started = time.monotonic()

    while pending:
        landed = [name for name in pending if on_pypi(name, version, refresh=True) is True]
        for name in landed:
            waited = time.monotonic() - started
            log(f"  {name} {version} is live ({waited / 60:.1f} min)")
        pending = [name for name in pending if name not in landed]
        if not pending:
            return []

        if time.monotonic() - started > timeout:
            return pending

        remaining = (timeout - (time.monotonic() - started)) / 60
        log(f"  waiting for {', '.join(pending)} ... ({remaining:.0f} min left before giving up)")
        time.sleep(interval)

    return []
