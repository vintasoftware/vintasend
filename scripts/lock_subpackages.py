#!/usr/bin/env python3
"""Relock, commit and push the version bump inside every vintasend-* subpackage.

`bump_version.py` rewrites each submodule's pyproject.toml, which leaves its
poetry.lock stale, and every submodule is its own git repository -- so after a
bump the release sits uncommitted in a dozen separate checkouts. This script is
the walk through them:

    1. poetry lock                          regenerate the stale lock
    2. git add pyproject.toml poetry.lock
    3. git commit
    4. git push origin <branch>

The family does not release in one go. A package can only be locked once every
vintasend it depends on is installable from PyPI, which splits the release into
waves: the root first, then the packages that only need the root, then the ones
that depend on those -- `vintasend-django-templates-manager` cannot lock until
`vintasend-managed-templates` is published. So this script handles one wave per
run: it locks what is ready, reports what is waiting, and you come back to it
after `tag_subpackages.py` has put that wave on PyPI.

    scripts/lock_subpackages.py --dry-run     # run every check, change nothing
    scripts/lock_subpackages.py               # check all, show the plan, ask, then go
    scripts/lock_subpackages.py --only vintasend-django
    scripts/lock_subpackages.py --no-push     # lock and commit, push by hand

Nothing here is irreversible: an unpushed commit can be amended and a pushed one
reverted, unlike a tag whose push uploads to PyPI. So this script -- unlike the
tagging ones -- keeps going past a package that fails, and is safe to re-run.
A package whose bump is already committed and pushed is reported and skipped.

Tag each wave with `scripts/tag_subpackages.py` once this succeeds.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from _git import (
    changed_paths,
    confirm,
    current_branch,
    git,
    head_is_pushed,
)
from _packages import (
    NAME_LINE_RE,
    VERSION_LINE_RE,
    Package,
    PackageError,
    find_packages,
    missing_submodules,
    release_waves,
    sibling_deps,
    wave_lines,
)
from _pypi import on_pypi


# The only files this script ever stages. Anything else a checkout happens to
# carry is the author's business, not the release's.
RELEASE_FILES = ("pyproject.toml", "poetry.lock")

# Resolving a whole dependency graph from a cold cache is slow, and a release
# run that times out half way through leaves a rewritten lock behind.
LOCK_TIMEOUT = 900


def locked_siblings(pkg: Package) -> dict[str, str]:
    """The vintasend-* versions written into this package's poetry.lock.

    Read back after locking: a resolver that fell through to the previous
    release produces a perfectly valid lock file, and the only visible sign is
    the version recorded here.
    """
    lock = pkg.dir / "poetry.lock"
    if not lock.exists():
        return {}

    versions: dict[str, str] = {}
    name = ""
    for line in lock.read_text(encoding="utf-8").splitlines():
        match = NAME_LINE_RE.match(line)
        if match:
            name = match.group("name")
            continue
        version_match = VERSION_LINE_RE.match(line)
        if version_match and name.startswith("vintasend"):
            versions.setdefault(name, version_match.group("version"))
            name = ""
    return versions


def check(
    pkg: Package, version: str, args: argparse.Namespace
) -> tuple[list[str], list[str], list[str]]:
    """Sort out what this package needs: (problems, waiting on, worth saying).

    A problem has to be fixed by hand. Waiting on is a list of sibling packages
    that are not published yet, which is not a mistake -- it is this package's
    turn coming later.
    """
    problems: list[str] = []
    waiting: list[str] = []
    notes: list[str] = []

    if pkg.version != version:
        problems.append(
            f"is at {pkg.version}, not {version} -- run `scripts/bump_version.py` first"
        )

    deps = sibling_deps(pkg)

    local = [f"pyproject.toml:{dep.lineno}: {dep.line}" for dep in deps if dep.local]
    if local:
        problems.append(
            "depends on vintasend through a local path, so the built package could never "
            "resolve it:\n            "
            + "\n            ".join(local)
            + "\n            restore the version pin (`scripts/prepare_release.py --revert-only` "
            "undoes a linked run)"
        )

    for dep in deps:
        if dep.local:
            continue
        if dep.version != version:
            problems.append(
                f"pins {dep.name} at {dep.version}, not {version} -- the family releases in step"
            )
            continue
        if args.no_pypi_check:
            continue
        published = on_pypi(dep.name, version)
        if published is False:
            waiting.append(dep.name)
        elif published is None:
            notes.append(f"could not reach PyPI to confirm {dep.name} {version} is published")

    branch = current_branch(pkg.dir)
    if branch == "HEAD":
        problems.append(
            f"is in detached HEAD, so a commit here would belong to no branch "
            f"-- run `git -C {pkg.rel} checkout main`"
        )

    code, _ = git(["remote", "get-url", "origin"], pkg.dir)
    if code != 0:
        problems.append("has no `origin` remote to push to")

    extra = [path for path in changed_paths(pkg.dir) if path not in RELEASE_FILES]
    if extra:
        if args.ignore_other_changes:
            notes.append(
                "leaving unrelated changes uncommitted: "
                + ", ".join(extra)
                + " (tag_subpackages.py will refuse this package until they are dealt with)"
            )
        else:
            problems.append(
                "has changes outside the release files: "
                + ", ".join(extra)
                + "\n            commit or stash them, or pass --ignore-other-changes to stage "
                "only pyproject.toml and poetry.lock"
            )

    return problems, waiting, notes


def poetry_available() -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["poetry", "--version"],  # noqa: S607 -- poetry off PATH is the normal invocation
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def relock(pkg: Package) -> tuple[bool, str]:
    """Run `poetry lock` in the package directory."""
    try:
        result = subprocess.run(  # noqa: S603
            ["poetry", "lock"],  # noqa: S607 -- poetry off PATH is the normal invocation
            cwd=pkg.dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=LOCK_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"poetry lock did not finish within {LOCK_TIMEOUT}s"

    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        # The tail carries the resolver's actual complaint; the head is progress.
        tail = "\n".join(output.splitlines()[-8:])
        return False, f"poetry lock failed:\n            {tail}"
    return True, ""


def release_package(pkg: Package, version: str, args: argparse.Namespace) -> tuple[str, str]:
    """Lock, commit and push one package. Returns a (status, detail) pair."""
    ok, why = relock(pkg)
    if not ok:
        return "FAIL", why

    locked = locked_siblings(pkg)
    stale = {name: found for name, found in locked.items() if found != version}
    if stale:
        listing = ", ".join(f"{name} {found}" for name, found in sorted(stale.items()))
        return "FAIL", (
            f"poetry.lock resolved {listing}, not {version} -- the lock would ship against "
            "the wrong release"
        )

    present = [name for name in RELEASE_FILES if (pkg.dir / name).exists()]
    code, out = git(["add", "--", *present], pkg.dir)
    if code != 0:
        return "FAIL", f"could not stage {', '.join(present)}: {out}"

    staged, _ = git(["diff", "--cached", "--quiet"], pkg.dir)
    if staged != 0:
        message = args.message.format(name=pkg.name, version=version)
        code, out = git(["commit", "-m", message], pkg.dir)
        if code != 0:
            return "FAIL", f"could not commit: {out}"
        committed = True
    else:
        committed = False

    if args.no_push:
        return ("committed" if committed else "up to date"), "push skipped (--no-push)"

    pushed, _ = head_is_pushed(pkg.dir)
    if not committed and pushed:
        return "up to date", "already committed and pushed"

    branch = current_branch(pkg.dir)
    code, out = git(["push", "origin", branch], pkg.dir)
    if code != 0:
        return "FAIL", f"committed, but the push failed: {out}"

    return ("committed" if committed else "pushed"), f"pushed to origin/{branch}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Relock, commit and push the version bump in every vintasend subpackage.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run every check and print the plan, change nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not prompt before committing (for automation)"
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        help="restrict to this package (repeatable)",
    )
    parser.add_argument(
        "--no-push", action="store_true", help="lock and commit, but leave the pushing to you"
    )
    parser.add_argument(
        "--ignore-other-changes",
        action="store_true",
        help="proceed in a checkout carrying unrelated edits, staging only the release files",
    )
    parser.add_argument(
        "--no-pypi-check",
        action="store_true",
        help="do not ask PyPI whether the pinned vintasend versions are published yet; "
        "every package is then treated as ready, whatever the release order says",
    )
    parser.add_argument(
        "--message",
        default="chore(release): {name} {version}",
        help="commit message; {name} and {version} are substituted",
    )
    args = parser.parse_args()

    if not poetry_available():
        print("error: `poetry` not found on PATH", file=sys.stderr)
        return 1

    try:
        missing = missing_submodules()
        if missing:
            raise PackageError(
                "these submodules are not checked out, so they cannot be locked: "
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
    wave_of = {pkg.name: number for number, wave in enumerate(waves, start=1) for pkg in wave}

    # The root has no sibling pin to wait on and is locked by hand before its own
    # release, so it is not this script's job.
    subpackages = [p for p in packages if p.rel != "."]
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {p.name for p in subpackages}
        if unknown:
            print(f"error: unknown package(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"known: {', '.join(sorted(p.name for p in subpackages))}", file=sys.stderr)
            return 1
        subpackages = [p for p in subpackages if p.name in wanted]

    print(f"root vintasend is at {version}\n")
    for line in wave_lines(waves):
        print(line)
    if not args.no_pypi_check:
        print("\nasking PyPI which of those are published ...")

    ready: list[Package] = []
    deferred: dict[str, list[str]] = {}
    blocked: dict[str, list[str]] = {}
    remarks: dict[str, list[str]] = {}

    for pkg in subpackages:
        problems, waiting, notes = check(pkg, version, args)
        if notes:
            remarks[pkg.name] = notes
        if problems:
            blocked[pkg.name] = problems
        if waiting:
            deferred[pkg.name] = waiting
        elif not problems:
            ready.append(pkg)

    print()
    for pkg in subpackages:
        wave = f"wave {wave_of[pkg.name]}"
        if pkg.name in deferred:
            waits = ", ".join(deferred[pkg.name])
            print(f"  [ later ] {pkg.name:<38} {wave}: waits for {waits}")
        elif pkg.name in blocked:
            print(f"  [BLOCKED] {pkg.name:<38} {wave}")
        else:
            print(f"  [  ok   ] {pkg.name:<38} {wave}, on {current_branch(pkg.dir)}")
        for problem in blocked.get(pkg.name, []):
            print(f"            {problem}")
        for note in remarks.get(pkg.name, []):
            print(f"            note: {note}")

    # A package waiting on a later wave can be fixed later too: it is not
    # published yet either way, so its problems do not stop the wave that is
    # ready to go now.
    blocked_now = [name for name in blocked if name not in deferred]
    if blocked_now:
        print(
            f"\nrefusing to start: {len(blocked_now)} package(s) in this wave are not ready.\n"
            "Fix them and re-run -- or use --only to work through the rest meanwhile."
        )
        return 1

    if not ready:
        print("\nnothing to lock in this run.")
        if deferred:
            first = min(wave_of[name] for name in deferred)
            print(
                f"Every remaining package is in wave {first} or later, waiting on a vintasend "
                "that is not on PyPI yet.\nPublish the wave before it, then re-run."
            )
        return 1

    later = sorted(deferred)
    print(f"\n  this run handles {len(ready)} package(s): ", end="")
    print(", ".join(p.name for p in ready))
    if later:
        print(f"  left for a later run: {', '.join(later)}")
    print(f"  each one: poetry lock, commit {' + '.join(RELEASE_FILES)}, ", end="")
    print("then push" if not args.no_push else "and stop (--no-push)")
    print(f'  commit message: "{args.message.format(name="<package>", version=version)}"')
    print("  none of this is irreversible; the tags come afterwards.")

    if args.dry_run:
        print("\ndry run: nothing locked, committed or pushed")
        return 0

    if not args.yes and not confirm(f"\nlock and commit {len(ready)} package(s) at {version}?"):
        print("aborted; nothing changed")
        return 1

    print()
    failed = []
    for pkg in ready:
        # Locking a cold graph takes a while, so say which package is holding
        # things up, then overwrite that line with the outcome.
        progress = f"  [ .... ] {pkg.name:<38} poetry lock ..."
        print(progress, end="", flush=True)
        status, detail = release_package(pkg, version, args)
        if status == "FAIL":
            failed.append(pkg.name)
            print("\r" + f"  [ FAIL ] {pkg.name:<38}".ljust(len(progress)))
            print(f"            {detail}")
        else:
            print("\r" + f"  [  ok  ] {pkg.name:<38} {status}: {detail}".ljust(len(progress)))

    if failed:
        print(
            f"\n{len(ready) - len(failed)} of {len(ready)} package(s) done. "
            f"Failed: {', '.join(failed)}"
        )
        print("Re-run once they are fixed; the packages that succeeded will report themselves")
        print("as up to date rather than committing again.")
        return 1

    print(f"\nall {len(ready)} package(s) in this wave locked, committed and pushed at {version}")
    if args.no_push:
        print("nothing was pushed (--no-push): push each one before tagging")

    print("\nnext: scripts/tag_subpackages.py")
    if later:
        print(
            "then, once this wave is live on PyPI, re-run this script for the rest: "
            + ", ".join(later)
        )
    print(
        "note: the superproject's gitlinks now point at the old submodule commits. "
        "Commit them here when you are ready:"
    )
    print(f"       git add {' '.join(p.rel for p in ready)} && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
