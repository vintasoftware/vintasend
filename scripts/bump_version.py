#!/usr/bin/env python3
"""Bump every vintasend package to one shared version number.

The whole family -- this repo plus each `vintasend-*` submodule -- releases in
lockstep, so every `pyproject.toml` carries the same `version`. This script is
the one place that number moves.

The root package is the source of truth: the new version is computed from
`vintasend`'s current version, then written to every package, whether it
declares its version under `[project]` (PEP 621) or `[tool.poetry]` (legacy).

Cross-package pins move with it. A submodule that depends on `vintasend = "^2.0.0"`
gets `"^2.1.0"`, keeping whatever operator it already used, because a lockstep
release that left the pins behind would resolve to the previous release.

    scripts/bump_version.py minor          # 2.0.0 -> 2.1.0 everywhere
    scripts/bump_version.py 3.0.0rc1       # an explicit version
    scripts/bump_version.py patch --dry-run

Editing is line-scoped rather than a TOML round-trip so comments, key order and
whitespace survive untouched.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

from _packages import (
    TABLE_RE,
    VERSION_LINE_RE,
    VERSION_TABLES,
    Package,
    PackageError,
    find_packages,
    missing_submodules,
)


# PEP 440 subset: three numeric segments plus an optional pre/post/dev suffix.
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)((?:a|b|rc|\.post|\.dev)\d+)?$")

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


@dataclass
class Change:
    lineno: int
    before: str
    after: str


def parse_version(raw: str) -> tuple[int, int, int, str]:
    match = VERSION_RE.match(raw.strip())
    if not match:
        raise PackageError(f"cannot parse version {raw!r}; expected MAJOR.MINOR.PATCH")
    major, minor, patch, suffix = match.groups()
    return int(major), int(minor), int(patch), suffix or ""


def next_version(current: str, part: str) -> str:
    """Return `current` bumped by `part`, or `part` itself if it is a version."""
    if part not in ("major", "minor", "patch"):
        # An explicit target. Validate it so a typo fails here rather than at
        # `poetry build` time, after every file is already rewritten.
        parse_version(part)
        return part

    major, minor, patch, suffix = parse_version(current)
    if suffix:
        # 2.1.0rc1 -> `patch` means "drop the pre-release marker and ship 2.1.0",
        # which is what a release candidate graduating actually needs.
        if part == "patch":
            return f"{major}.{minor}.{patch}"
        raise PackageError(
            f"root version {current!r} is a pre-release; bump it with an explicit "
            f"version, or use `patch` to graduate it to {major}.{minor}.{patch}"
        )

    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def rewrite(pkg: Package, new_version: str, update_pins: bool) -> None:
    """Compute the new file text and record every line it changes."""
    original = pkg.path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    out: list[str] = []
    table = ""
    version_done = False

    for index, line in enumerate(lines, start=1):
        header = TABLE_RE.match(line.rstrip("\n"))
        if header:
            table = header.group(1).strip()
            out.append(line)
            continue

        updated = line

        # The package's own version, taken only from its declaring table and
        # only once -- a later `version = ...` under some tool's config is not
        # this package's version.
        if not version_done and table in VERSION_TABLES:
            match = VERSION_LINE_RE.match(line)
            if match:
                # Splice rather than re.sub: everything past the matched value --
                # a trailing comment, for instance -- carries over untouched.
                quote = match.group("quote")
                updated = f"{match.group('prefix')}{quote}{new_version}{quote}{line[match.end() :]}"
                version_done = True

        if update_pins and updated is line and table.endswith("dependencies"):
            pin = PIN_RE.match(line)
            if pin:
                quote, op = pin.group("quote"), pin.group("op")
                updated = f"{pin.group('prefix')}{quote}{op}{new_version}{quote}{line[pin.end() :]}"
            elif WHEEL_RE.search(line):
                # A path pin at a built wheel: the version is in the filename.
                updated = WHEEL_RE.sub(rf"\g<name>-{new_version}-\g<tail>", line)

        if updated != line:
            pkg.changes.append(Change(index, line.rstrip("\n"), updated.rstrip("\n")))
        out.append(updated)

    if not version_done:
        raise PackageError(
            f"{pkg.rel}/pyproject.toml: could not locate its version line to rewrite"
        )

    pkg.new_text = "".join(out)


def verify(packages: list[Package], new_version: str) -> None:
    """Re-parse what was written and assert the shared-version invariant."""
    try:
        import tomllib  # type: ignore[import-not-found,import-untyped]  # stdlib on 3.11+
    except ImportError:  # Python 3.10, which this repo still supports
        print("note: python <3.11, skipping the TOML re-parse check", file=sys.stderr)
        return

    versions = {}
    for pkg in packages:
        with pkg.path.open("rb") as handle:
            data = tomllib.load(handle)
        found = data.get("project", {}).get("version") or data.get("tool", {}).get(
            "poetry", {}
        ).get("version")
        versions[pkg.rel] = found

    disagree = {rel: v for rel, v in versions.items() if v != new_version}
    if disagree:
        raise PackageError(f"after writing, these packages are not at {new_version}: {disagree}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump every vintasend package to one shared version.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("    scripts/")[0].strip(),
    )
    parser.add_argument(
        "part",
        help="major, minor, patch, or an explicit version such as 3.0.0 / 2.1.0rc1",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff without writing anything",
    )
    parser.add_argument(
        "--no-pins",
        action="store_true",
        help="bump versions only, leaving cross-package vintasend pins alone",
    )
    args = parser.parse_args()

    try:
        missing = missing_submodules()
        if missing:
            raise PackageError(
                "these submodules are not checked out, so they cannot be bumped: "
                + ", ".join(missing)
                + "\nrun `git submodule update --init` first, or every package will not share a version"
            )

        packages = find_packages()
        root = next((p for p in packages if p.rel == "."), None)
        if root is None:
            raise PackageError("no root pyproject.toml found")

        new_version = next_version(root.version, args.part)

        for pkg in packages:
            rewrite(pkg, new_version, update_pins=not args.no_pins)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    stale = [p for p in packages if p.version != root.version]
    print(f"root vintasend {root.version} -> {new_version}\n")
    for pkg in packages:
        touched = len(pkg.changes)
        # Flag only packages that were off the root version, not the bump itself.
        note = "  <- was out of step" if pkg.version != root.version else ""
        print(f"  {pkg.name:<38} {pkg.version:>10} -> {new_version:<10} {touched} line(s){note}")
        for change in pkg.changes:
            print(f"      {pkg.rel}/pyproject.toml:{change.lineno}")
            print(f"        - {change.before.strip()}")
            print(f"        + {change.after.strip()}")

    if stale:
        print(
            "\nnote: these packages were not on the root version and are being pulled "
            "into line: " + ", ".join(p.name for p in stale)
        )

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    for pkg in packages:
        pkg.path.write_text(pkg.new_text, encoding="utf-8")

    try:
        verify(packages, new_version)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\nwrote {len(packages)} pyproject.toml files at {new_version}")
    report_stale_locks(packages, new_version, pins_moved=not args.no_pins)
    return 0


def report_stale_locks(packages: list[Package], new_version: str, pins_moved: bool) -> None:
    """Explain the lock files this bump just invalidated, and the order to fix them.

    Poetry hashes pyproject.toml into poetry.lock, so every rewritten file leaves
    its lock stale and `poetry install` refuses to run -- which is what CI does.

    The root can be relocked immediately. The submodules cannot: their pins now
    point at a vintasend that is not on PyPI yet, so `poetry lock` there has
    nothing to resolve against until the root release publishes.
    """
    locked = [p for p in packages if (p.path.parent / "poetry.lock").exists()]
    if not locked:
        return

    root = [p for p in locked if p.rel == "."]
    downstream = [p for p in locked if p.rel != "."]

    print(f"\nstale lock files ({len(locked)}): poetry.lock embeds a hash of pyproject.toml,")
    print("so `poetry install` fails in each of these until it is regenerated.")

    if root:
        print("\n  1. relock the root now -- it resolves against its own deps only:")
        print("       poetry lock")

    if downstream and pins_moved:
        print(f"\n  2. release the root first. The submodules now pin vintasend {new_version},")
        print("     which does not exist on PyPI yet, so relocking them before the root")
        print("     publishes will fail to resolve. After it is live:")
        for pkg in downstream:
            print(f"       (cd {pkg.rel} && poetry lock)")
    elif downstream:
        print("\n  2. relock each submodule:")
        for pkg in downstream:
            print(f"       (cd {pkg.rel} && poetry lock)")

    print(
        "\neach submodule is its own git repo: review and commit there, then tag "
        f"v{new_version} per the release-package skill"
    )


if __name__ == "__main__":
    sys.exit(main())
