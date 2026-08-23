#!/usr/bin/env python3
"""Run the whole vintasend release, wave by wave, without babysitting it.

The steps are the ones you would run by hand, in the only order that works:

    1. tag_release.py          tag the root and push -- publish.yml uploads it
    2. wait                    until that version is installable from PyPI
    3. lock_subpackages.py     relock, commit and push the wave that is now ready
    4. tag_subpackages.py      tag and push that wave -- each publish.yml runs
    5. wait                    until every package in the wave is on PyPI
    6. back to 3 for the next wave, until nothing is left

The waiting is what makes this a script rather than a list. A subpackage pins
`vintasend` at the version being released, so `poetry lock` cannot resolve until
the root is actually live, and `vintasend-django-templates-manager` cannot
resolve until `vintasend-managed-templates` is. Each wait is minutes long: a tag
push only starts a build, which runs the package's test matrix before uploading.

    scripts/release_all.py --dry-run   # show the plan and run every check, change nothing
    scripts/release_all.py             # ask once, then run the whole thing
    scripts/release_all.py --yes       # do not ask (for automation)

This is the irreversible one. It publishes the entire family, and a version that
reaches PyPI can never be replaced. Everything before the confirmation is a
check; everything after it uploads.

Stopping is safe. A package already on PyPI, or already tagged on origin, is
detected and skipped, so re-running after a failure picks up where it stopped
rather than trying to publish twice.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from _git import remote_tag_exists
from _packages import (
    REPO_ROOT,
    Package,
    PackageError,
    find_packages,
    missing_submodules,
    release_waves,
    wave_lines,
)
from _pypi import on_pypi, wait_for_pypi


SCRIPTS = Path(__file__).resolve().parent

# A publish runs the package's full test matrix before uploading, and the slower
# packages test across five interpreters. Generous, because giving up early on a
# release that is still building is worse than waiting.
DEFAULT_TIMEOUT_MINUTES = 45.0
DEFAULT_POLL_SECONDS = 20.0


def run_script(name: str, *args: str) -> int:
    """Run one of the sibling release scripts, letting its output through.

    Not captured: these runs are long, and a silent terminal during a publish is
    exactly when you want to see what is happening.
    """
    argv = [sys.executable, str(SCRIPTS / name), *args]
    print(f"\n$ scripts/{name} {' '.join(args)}\n", flush=True)
    return subprocess.run(argv, cwd=REPO_ROOT, check=False).returncode  # noqa: S603


def published(pkg: Package, version: str) -> bool:
    return on_pypi(pkg.name, version) is True


def release_root(root: Package, version: str, args: argparse.Namespace) -> tuple[bool, str]:
    """Tag and publish the root package. Returns (ok, what happened)."""
    if published(root, version):
        return True, f"{root.name} {version} is already on PyPI"

    if remote_tag_exists(REPO_ROOT, f"v{version}"):
        # The tag is out but the version is not installable: either the build is
        # still running or it failed. Either way, pushing is not the fix.
        return False, (
            f"v{version} is already on origin but {root.name} {version} is not on PyPI.\n"
            "  Check the workflow (`gh run list`). If it failed, fix forward and bump to the\n"
            "  next patch -- do not delete and re-push a tag that may have published."
        )

    if run_script("tag_release.py", "--yes") != 0:
        return False, "tag_release.py failed; nothing else has been touched"

    print(f"\nwaiting for {root.name} {version} to reach PyPI ...")
    late = wait_for_pypi([root.name], version, args.timeout * 60, args.poll)
    if late:
        return False, (
            f"{root.name} {version} did not reach PyPI within {args.timeout:.0f} minutes.\n"
            "  The tag is pushed, so check the workflow rather than re-running blindly."
        )
    return True, f"{root.name} {version} published"


def release_wave(
    wave: list[Package], number: int, version: str, args: argparse.Namespace
) -> tuple[bool, str]:
    """Lock, commit, tag and publish one wave of subpackages."""
    todo = [pkg for pkg in wave if not published(pkg, version)]
    if not todo:
        return True, f"wave {number} is already on PyPI"

    only: list[str] = []
    for pkg in todo:
        only += ["--only", pkg.name]

    if run_script("lock_subpackages.py", "--yes", *only) != 0:
        return False, f"lock_subpackages.py failed on wave {number}; no tag was pushed"

    if run_script("tag_subpackages.py", "--yes", *only) != 0:
        return False, (
            f"tag_subpackages.py failed on wave {number}. Any tag it did push is already\n"
            "  publishing; re-run this script once the rest is fixed."
        )

    print(f"\nwaiting for wave {number} to reach PyPI ...")
    late = wait_for_pypi([pkg.name for pkg in todo], version, args.timeout * 60, args.poll)
    if late:
        return False, (
            f"these did not reach PyPI within {args.timeout:.0f} minutes: {', '.join(late)}\n"
            "  Their tags are pushed, so check each workflow (`gh run list`) before re-running."
        )
    return True, f"wave {number} published: {', '.join(pkg.name for pkg in todo)}"


def confirm_release(version: str, waves: list[list[Package]]) -> bool:
    """The one prompt. Typing the version is deliberate -- y is too easy."""
    if not sys.stdin.isatty():
        print("stdin is not a terminal; re-run with --yes to confirm non-interactively")
        return False

    total = sum(len(wave) for wave in waves)
    print(
        f"\nthis publishes {total} packages at {version} across {len(waves)} waves, permanently.\n"
        "Every upload is immutable: none of these versions can ever be replaced."
    )
    try:
        typed = input(f"type {version} to go ahead: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return typed == version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Release the whole vintasend family, wave by wave.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and run each script's own checks, publishing nothing",
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not prompt before publishing (for automation)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_MINUTES,
        metavar="MINUTES",
        help=f"how long to wait for a wave to reach PyPI (default {DEFAULT_TIMEOUT_MINUTES:.0f})",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=DEFAULT_POLL_SECONDS,
        metavar="SECONDS",
        help=f"how often to re-check PyPI while waiting (default {DEFAULT_POLL_SECONDS:.0f})",
    )
    args = parser.parse_args()

    try:
        missing = missing_submodules()
        if missing:
            raise PackageError(
                "these submodules are not checked out, so they cannot be released: "
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
    print(f"releasing the vintasend family at {version}\n")
    for line in wave_lines(waves):
        print(line)

    print("\nalready on PyPI:")
    done = [pkg.name for pkg in packages if published(pkg, version)]
    print(f"  {', '.join(done) if done else '(nothing yet)'}")

    if args.dry_run:
        print("\n" + "=" * 78)
        print("DRY RUN -- running each script's own checks, publishing nothing")
        print("=" * 78)
        if published(root, version):
            print(f"\nskipping tag_release.py: {root.name} {version} is already on PyPI")
        else:
            run_script("tag_release.py", "--dry-run")
        run_script("lock_subpackages.py", "--dry-run")
        run_script("tag_subpackages.py", "--dry-run")
        print(
            "\nreading this dry run: the subpackage scripts only ever report the wave that is\n"
            "ready now -- later waves become visible as each one publishes. And nothing has been\n"
            "locked here, so a package that tag_subpackages.py calls blocked only because its\n"
            "bump is uncommitted is fine: in a real run lock_subpackages.py commits it first."
        )
        return 0

    if not args.yes and not confirm_release(version, waves):
        print("aborted; nothing published")
        return 1

    started = time.monotonic()
    for number, wave in enumerate(waves, start=1):
        print("\n" + "=" * 78)
        print(f"WAVE {number} of {len(waves)}: {', '.join(pkg.name for pkg in wave)}")
        print("=" * 78)

        # The root is tagged by tag_release.py, which also writes the GitHub
        # release; everything else goes through the subpackage scripts.
        if root in wave:
            ok, detail = release_root(root, version, args)
            print(f"\n{detail}")
            if not ok:
                print(f"\nstopped in wave {number}. Fix the above and re-run -- what already")
                print("published is detected and skipped.")
                return 1

        rest = [pkg for pkg in wave if pkg is not root]
        if rest:
            ok, detail = release_wave(rest, number, version, args)
            print(f"\n{detail}")
            if not ok:
                print(f"\nstopped in wave {number}. Fix the above and re-run -- what already")
                print("published is detected and skipped.")
                return 1

    elapsed = (time.monotonic() - started) / 60
    print("\n" + "=" * 78)
    print(f"released {len(packages)} packages at {version} in {elapsed:.0f} minutes")
    print("=" * 78)
    print("\nthe submodules moved, so the superproject still points at their old commits:")
    print(
        "       git add implementations tools && git commit -m 'chore: submodules at "
        f"{version}' && git push"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
