#!/usr/bin/env python3
"""Tag the root vintasend package and publish its GitHub release.

Pushing the tag is what ships the release: `.github/workflows/publish.yml`
triggers on `v*`, runs the matrix, builds with Poetry and uploads to PyPI. A
version that reaches PyPI is immutable -- it can never be re-uploaded, and a
deleted tag does not undo it. So every check runs before the push, and the push
itself needs a confirmation.

    scripts/tag_release.py --dry-run     # run the checks, change nothing
    scripts/tag_release.py               # check, show the plan, ask, then push
    scripts/tag_release.py --yes         # skip the prompt (for automation)

The version comes from pyproject.toml, never from an argument: Poetry builds
what pyproject says, so a tag that disagrees with it publishes the wrong number
and PyPI rejects it as a duplicate. Bump with `scripts/bump_version.py` first.

Tag the submodules with `scripts/tag_subpackages.py` after this one succeeds --
downstream packages cannot depend on a vintasend that is not published yet.
"""

from __future__ import annotations

import argparse
import re
import subprocess
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
from _packages import REPO_ROOT, PackageError, find_packages


RELEASE_NOTES = REPO_ROOT / "RELEASE_NOTES.md"

# Releases are cut from main. The skill is explicit about not tagging a branch.
RELEASE_BRANCH = "main"


def release_notes_section(version: str) -> str:
    """The RELEASE_NOTES.md entry for `version`, used as the GitHub release body.

    Entries look like `## Version 2.1.0 (2026-08-20)` and run until the next
    `## ` heading.
    """
    if not RELEASE_NOTES.exists():
        raise PackageError(f"{RELEASE_NOTES.name} not found")

    text = RELEASE_NOTES.read_text(encoding="utf-8")
    pattern = rf"^## Version {re.escape(version)}\b.*?$"
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise PackageError(
            f"{RELEASE_NOTES.name} has no `## Version {version}` entry.\n"
            "Tagging without release notes is how a downstream implementer finds out about a "
            "seam change from a TypeError. Add the entry first -- see the release-package skill."
        )

    start = match.end()
    following = re.search(r"^## ", text[start:], re.MULTILINE)
    body = text[start : start + following.start()] if following else text[start:]
    return body.strip()


def preflight(version: str, tag: str) -> list[str]:
    """Every reason this release must not be tagged. Empty means go."""
    problems = []

    branch = current_branch(REPO_ROOT)
    if branch != RELEASE_BRANCH:
        problems.append(f"on branch {branch!r}, not {RELEASE_BRANCH!r} -- do not tag from a branch")

    clean, dirty = is_clean(REPO_ROOT)
    if not clean:
        problems.append(
            "working tree has uncommitted changes, so the tag would not name what you tested:\n"
            + "\n".join(f"      {line}" for line in dirty.splitlines())
        )

    pushed, why = head_is_pushed(REPO_ROOT)
    if not pushed:
        problems.append(why)

    if local_tag_exists(REPO_ROOT, tag):
        problems.append(f"tag {tag} already exists locally")
    if remote_tag_exists(REPO_ROOT, tag):
        problems.append(
            f"tag {tag} already exists on origin. If it published, that version is immutable on "
            "PyPI -- move forward to the next version rather than re-pushing."
        )

    # The lockstep invariant: every package shares one version. Tagging the root
    # at a version the submodules do not carry means tag_subpackages.py cannot
    # run afterwards.
    try:
        out_of_step = [p.name for p in find_packages() if p.version != version]
    except PackageError as exc:
        problems.append(str(exc))
    else:
        if out_of_step:
            problems.append(
                f"these packages are not at {version}: {', '.join(out_of_step)}\n"
                "      run `scripts/bump_version.py` so the whole family shares one version"
            )

    try:
        release_notes_section(version)
    except PackageError as exc:
        problems.append(str(exc))

    return problems


def gh_available() -> bool:
    try:
        result = subprocess.run(  # noqa: S603
            ["gh", "--version"],  # noqa: S607 -- gh off PATH is the normal invocation
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tag the root vintasend package and publish its GitHub release.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="run every check and print the plan, change nothing"
    )
    parser.add_argument(
        "--yes", action="store_true", help="do not prompt before pushing (for automation)"
    )
    parser.add_argument(
        "--no-release",
        action="store_true",
        help="push the tag but do not create the GitHub release",
    )
    args = parser.parse_args()

    try:
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

    print(f"fetching origin to check for an existing {tag} ...")
    fetch(REPO_ROOT)

    problems = preflight(version, tag)
    if problems:
        print(f"\ncannot tag {tag}:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    notes = release_notes_section(version)
    print(f"\n  package        {root.name}")
    print(f"  version        {version}   (from pyproject.toml)")
    print(f"  tag            {tag}   (annotated)")
    print(f"  commit         {head_sha(REPO_ROOT)[:12]} on {RELEASE_BRANCH}")
    print(f"  release notes  {len(notes.splitlines())} lines from {RELEASE_NOTES.name}")
    print(f"  github release {'skipped (--no-release)' if args.no_release else 'yes'}")
    print("\n  pushing this tag triggers publish.yml, which uploads to PyPI.")
    print("  that upload is permanent: the version can never be replaced.")

    if args.dry_run:
        print("\ndry run: nothing pushed")
        return 0

    if not args.yes and not confirm(f"\npush {tag} and publish {root.name} {version} to PyPI?"):
        print("aborted; nothing pushed")
        return 1

    code, out = git(["tag", "-a", tag, "-m", f"{root.name} {version}"], REPO_ROOT)
    if code != 0:
        print(f"error: could not create tag: {out}", file=sys.stderr)
        return 1
    print(f"created {tag}")

    code, out = git(["push", "origin", tag], REPO_ROOT)
    if code != 0:
        # Roll the local tag back so a retry is not blocked by a tag that never
        # reached the remote.
        git(["tag", "-d", tag], REPO_ROOT)
        print(f"error: could not push tag (local tag removed): {out}", file=sys.stderr)
        return 1
    print(f"pushed {tag} -- publish.yml is now running")

    if args.no_release:
        return 0

    if not gh_available():
        print("\nwarning: `gh` not found, so no GitHub release was created.")
        print(f"create it manually: gh release create {tag} --title '{tag}' --notes-file -")
        return 0

    notes_file = REPO_ROOT / f".release-notes-{version}.md"
    notes_file.write_text(notes, encoding="utf-8")
    try:
        result = subprocess.run(  # noqa: S603
            [  # noqa: S607 -- gh off PATH is the normal invocation
                "gh",
                "release",
                "create",
                tag,
                "--title",
                tag,
                "--notes-file",
                str(notes_file),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        notes_file.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"\nwarning: tag pushed, but the GitHub release failed:\n{result.stderr.strip()}")
        print(f"the PyPI publish is unaffected. Retry with: gh release create {tag}")
        return 1

    print(f"created GitHub release {tag}: {result.stdout.strip()}")
    print(f"\nnext: scripts/tag_subpackages.py, once {root.name} {version} is live on PyPI")
    return 0


if __name__ == "__main__":
    sys.exit(main())
