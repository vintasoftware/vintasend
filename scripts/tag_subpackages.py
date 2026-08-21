#!/usr/bin/env python3
"""Tag the vintasend-* subpackages that are ready, at the shared version.

Each submodule is its own git repository with its own `publish.yml`, triggered
by its own `v*` tag. This tags them at the one version the family shares and
pushes each tag, so a whole wave publishes together.

    scripts/tag_subpackages.py --dry-run     # run the checks, change nothing
    scripts/tag_subpackages.py               # check all, show the plan, ask, then push
    scripts/tag_subpackages.py --only vintasend-django

Run this *after* `scripts/tag_release.py`, and only once the root package is
actually live on PyPI: every subpackage pins `vintasend` at this version, so a
build that starts before the root is published cannot resolve its own dependency.

The same rule splits the subpackages into waves. `vintasend-managed-templates`
has to be on PyPI before `vintasend-django-templates-manager` can build against
it, so this script tags only the packages whose vintasend dependencies are all
published, and reports the rest as waiting. Once a wave is live, relock the next
one with `scripts/lock_subpackages.py` and run this again.

This script only tags. Getting the bump relocked, committed and pushed in each
submodule first is `scripts/lock_subpackages.py`; a package that still has that
pending is reported here as having uncommitted changes or an unpushed HEAD.

Every package in the wave is checked before any tag is pushed. A wave that stops
half way leaves some packages published and others not, which is far more
awkward to unpick than a run that refused to start.
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
from _packages import (
    Package,
    PackageError,
    find_packages,
    missing_submodules,
    release_waves,
    sibling_deps,
    wave_lines,
)
from _pypi import on_pypi


def waiting_on(pkg: Package, version: str, skip_pypi: bool) -> list[str]:
    """The vintasend packages this one needs on PyPI that are not there yet.

    Not a fault: it is this package's turn coming later. Tagging it now would
    start a build that cannot resolve its own dependency.
    """
    if skip_pypi:
        return []
    return [
        dep.name
        for dep in sibling_deps(pkg)
        if not dep.local and dep.version == version and on_pypi(dep.name, version) is False
    ]


def check(pkg: Package, version: str, tag: str) -> list[str]:
    """Every reason this package must not be tagged."""
    problems = []

    if pkg.version != version:
        problems.append(
            f"is at {pkg.version}, not {version} -- run `scripts/bump_version.py` first"
        )

    for dep in sibling_deps(pkg):
        if dep.local:
            # Tagging this publishes a package pointing at a directory that only
            # exists on this machine, and hides it from the wave ordering too:
            # a path dependency says nothing about what has to be released first.
            problems.append(
                f"depends on {dep.name} through a local path "
                f"(pyproject.toml:{dep.lineno}: {dep.line}) -- restore the version pin"
            )
        elif dep.version != version:
            problems.append(
                f"pins {dep.name} at {dep.version}, not {version} -- the family releases in step"
            )

    clean, dirty = is_clean(pkg.dir)
    if not clean:
        listing = ", ".join(line.strip() for line in dirty.splitlines()[:4])
        problems.append(f"has uncommitted changes ({listing}) -- scripts/lock_subpackages.py")

    pushed, why = head_is_pushed(pkg.dir)
    if not pushed:
        problems.append(why)

    if local_tag_exists(pkg.dir, tag):
        problems.append(f"already has a local tag {tag}")

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
    parser.add_argument(
        "--no-pypi-check",
        action="store_true",
        help="do not ask PyPI which vintasend versions are published; every package is then "
        "treated as ready, whatever the release order says",
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
        waves = release_waves(packages)
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

    print(f"root vintasend is at {version}; tagging as {tag}\n")
    for line in wave_lines(waves):
        print(line)
    wave_of = {pkg.name: number for number, wave in enumerate(waves, start=1) for pkg in wave}

    print("\nfetching each submodule's origin ...")
    for pkg in subpackages:
        fetch(pkg.dir)

    # Sort the packages three ways before checking anything: already released,
    # waiting for an earlier wave, and this run's work.
    done: list[Package] = []
    waiting: dict[str, list[str]] = {}
    candidates: list[Package] = []
    for pkg in subpackages:
        if remote_tag_exists(pkg.dir, tag):
            done.append(pkg)
            continue
        needs = waiting_on(pkg, version, args.no_pypi_check)
        if needs:
            waiting[pkg.name] = needs
        else:
            candidates.append(pkg)

    # Check the whole wave first: no tag is pushed until all of them can be.
    blocked: dict[str, list[str]] = {}
    for pkg in candidates:
        problems = check(pkg, version, tag)
        if problems:
            blocked[pkg.name] = problems

    print()
    for pkg in subpackages:
        wave = f"wave {wave_of[pkg.name]}"
        if pkg in done:
            print(f"  [ done  ] {pkg.name:<38} {wave}: {tag} is already on origin")
        elif pkg.name in waiting:
            print(f"  [ later ] {pkg.name:<38} {wave}: waits for {', '.join(waiting[pkg.name])}")
        elif pkg.name in blocked:
            print(f"  [BLOCKED] {pkg.name:<38} {wave}")
            for problem in blocked[pkg.name]:
                print(f"            {problem}")
        else:
            print(
                f"  [  ok   ] {pkg.name:<38} {wave}, "
                f"{head_sha(pkg.dir)[:12]} on {current_branch(pkg.dir)}"
            )

    if blocked:
        print(
            f"\nrefusing to tag anything: {len(blocked)} of {len(candidates)} package(s) in this "
            "wave are not releasable.\nA half-finished wave is harder to unpick than one that "
            "never started."
        )
        return 1

    if not candidates:
        if waiting:
            first = min(wave_of[name] for name in waiting)
            print(
                f"\nnothing to tag: every remaining package is in wave {first} or later, waiting "
                "on a vintasend\nthat is not on PyPI yet. Publish the wave before it first."
            )
            return 1
        print(f"\nnothing to tag: every selected package already has {tag} on origin")
        return 0

    subpackages = candidates

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
    if waiting:
        print(
            f"\nstill waiting on this wave: {', '.join(sorted(waiting))}\n"
            "once these publishes are live on PyPI, relock them with "
            "scripts/lock_subpackages.py\nand run this script again."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
