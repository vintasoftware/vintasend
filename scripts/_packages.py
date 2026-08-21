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
import textwrap
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

# A dependency line pinning a sibling package, e.g. `vintasend = "^2.0.0"`.
PIN_RE = re.compile(
    r"^(?P<prefix>\s*(?P<name>vintasend[\w-]*)\s*=\s*)"
    r'(?P<quote>["\'])(?P<op>[~^><=!]*)(?P<version>\d[^"\']*)(?P=quote)'
)

# A sibling pinned as a built wheel carries the version inside the filename,
# e.g. `vintasend = { path = "../../dist/vintasend-2.0.0-py3-none-any.whl" }`.
WHEEL_RE = re.compile(
    r"(?P<name>vintasend[\w_-]*)-(?P<version>\d[^-/\"']*)-(?P<tail>py3-none-any\.whl)"
)

# A sibling pinned as an inline table, e.g.
# `vintasend = { path = "../..", develop = true }`. Whether that table names a
# local path, a wheel or a git ref is read off `body` by the caller.
INLINE_DEP_RE = re.compile(r"^(?P<prefix>\s*(?P<name>vintasend[\w-]*)\s*=\s*)\{(?P<body>[^}]*)\}")

# Keys inside an inline table that make a dependency unpublishable: each names
# something outside PyPI, which an installed sdist has no way to reach.
LOCAL_DEP_KEYS = ("path", "file", "url", "git")


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


@dataclass
class SiblingDep:
    """One `vintasend*` dependency declared by a package."""

    name: str
    lineno: int
    line: str
    version: str = ""  # empty when declared as an inline table rather than a pin
    local: bool = False  # a path / file / url / git ref, so not installable from PyPI


def sibling_deps(pkg: Package) -> list[SiblingDep]:
    """Every `vintasend*` package this one depends on, however it is declared.

    Dev-group dependencies count: `poetry lock` resolves them too, so a sibling
    needed only by the test suite still has to be published first.
    """
    found = []
    for lineno, line in dependency_lines(pkg):
        inline = INLINE_DEP_RE.match(line)
        if inline:
            body = inline.group("body")
            local = any(re.search(rf"\b{key}\s*=", body) for key in LOCAL_DEP_KEYS)
            found.append(
                SiblingDep(name=inline.group("name"), lineno=lineno, line=line.strip(), local=local)
            )
            continue

        pin = PIN_RE.match(line)
        if pin:
            found.append(
                SiblingDep(
                    name=pin.group("name"),
                    lineno=lineno,
                    line=line.strip(),
                    version=pin.group("version"),
                )
            )

    return [dep for dep in found if dep.name != pkg.name]


def release_waves(packages: list[Package]) -> list[list[Package]]:
    """`packages` grouped into the order they have to be published in.

    A package can only be locked and released once every vintasend it depends on
    is installable from PyPI, so `vintasend-django-templates-manager` cannot go
    out until `vintasend-managed-templates` is live, which in turn waits on the
    root. Each wave is a set that can go together; the next one waits for it.
    """
    by_name = {p.name: p for p in packages}
    needs = {
        p.name: {d.name for d in sibling_deps(p) if d.name in by_name and d.name != p.name}
        for p in packages
    }

    waves: list[list[Package]] = []
    released: set[str] = set()
    while len(released) < len(packages):
        wave = [p for p in packages if p.name not in released and needs[p.name] <= released]
        if not wave:
            stuck = sorted(set(by_name) - released)
            raise PackageError(
                "these packages depend on each other in a cycle, so no release order exists: "
                + ", ".join(stuck)
            )
        waves.append(wave)
        released.update(p.name for p in wave)
    return waves


def wave_lines(waves: list[list[Package]]) -> list[str]:
    """The release order, ready to print. Both tagging scripts show the same map."""
    rendered = ["release order (each wave waits for the one before it to be live on PyPI):"]
    for number, wave in enumerate(waves, start=1):
        body = textwrap.fill(
            ", ".join(p.name for p in wave),
            width=84,
            subsequent_indent=" " * 12,
            break_on_hyphens=False,
            break_long_words=False,
        )
        rendered.append(f"  wave {number}  {body}")
    return rendered


def dependency_lines(pkg: Package) -> list[tuple[int, str]]:
    """Every line of `pkg`'s pyproject.toml that sits under a dependency table.

    Both `[tool.poetry.dependencies]` and `[project.optional-dependencies]` end
    in `dependencies`, which is what makes the suffix test enough here.
    """
    found = []
    table = ""
    for index, line in enumerate(pkg.path.read_text(encoding="utf-8").splitlines(), start=1):
        header = TABLE_RE.match(line)
        if header:
            table = header.group(1).strip()
            continue
        if table.endswith("dependencies"):
            found.append((index, line))
    return found
