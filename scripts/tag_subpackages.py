#!/usr/bin/env python3
"""Tag every vintasend-* subpackage at the shared version and push the tags.

Each submodule is its own git repository with its own `publish.yml`, triggered
by its own `v*` tag. This tags all of them at the one version the family shares
and pushes each tag, so the whole set publishes together.

    scripts/tag_subpackages.py --dry-run     # run the checks, change nothing
    scripts/tag_subpackages.py               # check all, show the plan, ask, then push
    scripts/tag_subpackages.py --only vintasend-django

Run this *after* `scripts/tag_release.py`, and only once the root package is
actually live on PyPI: every subpackage pins `vintasend` at this version, so a
build that starts before the root is published cannot resolve its own dependency.

Every package is checked before any tag is pushed. A release that stops half way
leaves some packages published and others not, which is far more awkward to
unpick than a run that refused to start.
"""

from __future__ import annotations

import argparse
import sys

from _git import (
    confirm,
    current_branch,
    fetch,
    git,
    head_is_pushed,
    head_sha,
    is_clean,
    local_tag_exists,
    remote_tag_exists,
)
from _packages import Package, PackageError, find_packages, missing_submodules


def check(pkg: Package, version: str, tag: str) -> list[str]:
    """Every reason this package must not be tagged."""
    problems = []

    if pkg.version != version:
        problems.append(
            f"is at {pkg.version}, not {version} -- run `scripts/bump_version.py` first"
        )

    clean, dirty = is_clean(pkg.dir)
    if not clean:
        listing = ", ".join(line.strip() for line in dirty.splitlines()[:4])
        problems.append(f"has uncommitted changes ({listing})")

    pushed, why = head_is_pushed(pkg.dir)
    if not pushed:
        problems.append(why)

    if local_tag_exists(pkg.dir, tag):
        problems.append(f"already has a local tag {tag}")
    if remote_tag_exists(pkg.dir, tag):
        problems.append(
            f"already has {tag} on origin -- if it published, that version is immutable on PyPI"
        )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tag every vintasend subpackage at the shared version and push the tags.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run every check and print the plan, change nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not prompt before pushing (for automation)"
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        help="restrict to this package (repeatable)",
    )
    args = parser.parse_args()

    try:
        missing = missing_submodules()
        if missing:
            raise PackageError(
                "these submodules are not checked out, so they cannot be tagged: "
                + ", ".join(missing)
                + "\nrun `git submodule update --init` first"
            )
        packages = find_packages()
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    root = next((p for p in packages if p.rel == "."), None)
    if root is None:
        print("error: no root pyproject.toml found", file=sys.stderr)
        return 1

    version = root.version
    tag = f"v{version}"

    # The root is tagged by tag_release.py, not here.
    subpackages = [p for p in packages if p.rel != "."]
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {p.name for p in subpackages}
        if unknown:
            print(f"error: unknown package(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"known: {', '.join(sorted(p.name for p in subpackages))}", file=sys.stderr)
            return 1
        subpackages = [p for p in subpackages if p.name in wanted]

    print(f"root vintasend is at {version}; tagging {len(subpackages)} subpackage(s) as {tag}\n")

    print("fetching each submodule's origin ...")
    for pkg in subpackages:
        fetch(pkg.dir)

    # Check everything first: no tag is pushed until all of them can be.
    blocked: dict[str, list[str]] = {}
    for pkg in subpackages:
        problems = check(pkg, version, tag)
        if problems:
            blocked[pkg.name] = problems

    print()
    for pkg in subpackages:
        if pkg.name in blocked:
            print(f"  [BLOCKED] {pkg.name}")
            for problem in blocked[pkg.name]:
                print(f"            {problem}")
        else:
            print(
                f"  [  ok   ] {pkg.name:<38} {head_sha(pkg.dir)[:12]} on {current_branch(pkg.dir)}"
            )

    if blocked:
        print(
            f"\nrefusing to tag anything: {len(blocked)} of {len(subpackages)} package(s) are not "
            "releasable.\nA half-finished release is harder to unpick than one that never started."
        )
        return 1

    print(f"\n  pushing {len(subpackages)} tags triggers each package's publish.yml.")
    print("  those uploads are permanent: these versions can never be replaced.")
    print(f"  make sure vintasend {version} is already live on PyPI, or every build")
    print("  below will fail to resolve its own vintasend dependency.")

    if args.dry_run:
        print("\ndry run: nothing pushed")
        return 0

    if not args.yes and not confirm(f"\npush {tag} to {len(subpackages)} repositories?"):
        print("aborted; nothing pushed")
        return 1

    print()
    failed = []
    for pkg in subpackages:
        code, out = git(["tag", "-a", tag, "-m", f"{pkg.name} {version}"], pkg.dir)
        if code != 0:
            print(f"  [FAIL] {pkg.name}: could not create tag: {out}")
            failed.append(pkg.name)
            continue

        code, out = git(["push", "origin", tag], pkg.dir)
        if code != 0:
            # Drop the local tag so a retry is not blocked by one that never
            # reached the remote.
            git(["tag", "-d", tag], pkg.dir)
            print(f"  [FAIL] {pkg.name}: could not push (local tag removed): {out}")
            failed.append(pkg.name)
            continue

        print(f"  [ ok ] {pkg.name:<38} pushed {tag}")

    if failed:
        print(
            f"\n{len(subpackages) - len(failed)} of {len(subpackages)} tags pushed. "
            f"Failed: {', '.join(failed)}"
        )
        print("The pushed tags are already publishing. Fix the failures and re-run;")
        print("packages that succeeded will report their tag as already on origin.")
        return 1

    print(f"\npushed {tag} to all {len(subpackages)} repositories -- each publish.yml is running")
    print("watch them with: gh run list  (in each submodule)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
