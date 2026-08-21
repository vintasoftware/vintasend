"""Shared discovery of the vintasend packages in this superproject.

Both `bump_version.py` and `prepare_release.py` need the same answers -- which
directories hold a Python package, what each one is called, and how its
`pyproject.toml` is laid out -- so the TOML line grammar lives here rather than
being duplicated and left to drift.

Editing is deliberately line-scoped rather than a TOML round-trip: comments, key
order and whitespace in these files are hand-maintained and worth preserving.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Where packages live. Anything without a pyproject.toml is not a Python package
# and is skipped -- which is how tools/vintasend-dashboard (a JS app) stays out.
PACKAGE_GLOBS = ("pyproject.toml", "implementations/*/pyproject.toml", "tools/*/pyproject.toml")

# Submodules deliberately outside the Python release. Listing them means an
# uninitialized submodule that is NOT here stops the caller, instead of being
# skipped in silence.
NON_PYTHON_SUBMODULES = frozenset({"tools/vintasend-dashboard"})

# Tables whose `name`/`version` keys describe the package itself. The repo uses
# both spellings: PEP 621 `[project]` and legacy `[tool.poetry]`.
VERSION_TABLES = ("project", "tool.poetry")

TABLE_RE = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*$")
VERSION_LINE_RE = re.compile(
    r'^(?P<prefix>\s*version\s*=\s*)(?P<quote>["\'])(?P<version>[^"\']*)(?P=quote)'
)
NAME_LINE_RE = re.compile(r'^\s*name\s*=\s*["\'](?P<name>[^"\']+)["\']')


class PackageError(RuntimeError):
    """A problem that must stop the caller before anything is written."""


@dataclass
class Package:
    path: Path  # the pyproject.toml itself
    name: str = ""
    version: str = ""
    changes: list = field(default_factory=list)
    new_text: str = ""

    @property
    def dir(self) -> Path:
        return self.path.parent

    @property
    def rel(self) -> str:
        return str(self.path.parent.relative_to(REPO_ROOT)) or "."


def read_metadata(pkg: Package) -> None:
    """Pull `name` and `version` out of the package's own declaring table."""
    table = ""
    for line in pkg.path.read_text(encoding="utf-8").splitlines():
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        if table not in VERSION_TABLES:
            continue
        match = VERSION_LINE_RE.match(line)
        if match and not pkg.version:
            pkg.version = match.group("version")
        name_match = NAME_LINE_RE.match(line)
        if name_match and not pkg.name:
            pkg.name = name_match.group("name")

    if not pkg.version:
        raise PackageError(
            f"{pkg.rel}/pyproject.toml declares no version under [project] or [tool.poetry]"
        )
    if not pkg.name:
        pkg.name = pkg.rel


def find_packages() -> list[Package]:
    """Every Python package in the superproject, root first."""
    seen: dict[Path, None] = {}
    for pattern in PACKAGE_GLOBS:
        for path in sorted(REPO_ROOT.glob(pattern)):
            seen.setdefault(path.resolve(), None)
    if not seen:
        raise PackageError("found no pyproject.toml files")

    packages = []
    for path in seen:
        pkg = Package(path=path)
        read_metadata(pkg)
        packages.append(pkg)
    # Root first, then submodules alphabetically, so output reads top-down.
    packages.sort(key=lambda p: (p.rel != ".", p.rel))
    return packages


def missing_submodules() -> list[str]:
    """Submodules that are not checked out and are not a known exclusion.

    An uninitialized submodule has no pyproject.toml, so it would be skipped in
    silence -- the one failure mode that breaks a whole-family guarantee without
    being visible.
    """
    try:
        result = subprocess.run(
            ["git", "submodule", "status"],  # noqa: S607 -- git off PATH is the normal invocation
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    missing = []
    for line in result.stdout.splitlines():
        if line.startswith("-"):
            path = line[1:].split()[1]
            if path not in NON_PYTHON_SUBMODULES:
                missing.append(path)
    return missing
