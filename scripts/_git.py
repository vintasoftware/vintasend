"""Git helpers shared by the tagging scripts.

Pushing a release tag is the irreversible step in this project: it triggers
`publish.yml`, and a version that reaches PyPI can never be re-uploaded. Every
helper here exists so the tagging scripts can refuse *before* that push rather
than diagnose afterwards.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def git(args: list[str], cwd: Path) -> tuple[int, str]:
    """Run a git command, returning its exit code and stripped combined output."""
    proc = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 -- git off PATH is the normal invocation
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def current_branch(cwd: Path) -> str:
    _, out = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return out


def head_sha(cwd: Path) -> str:
    _, out = git(["rev-parse", "HEAD"], cwd)
    return out


def is_clean(cwd: Path, ignore_submodules: bool = False) -> tuple[bool, str]:
    """Whether the working tree has no tracked modifications.

    Untracked files are ignored: they are not part of what the tag would point
    at. Staged or unstaged changes to tracked files are not, because the tag
    would name a commit that differs from the tree the gates were run against.

    `ignore_submodules` drops submodule entries -- both a moved gitlink and dirt
    inside the submodule's own checkout. The root package is released first, so
    at that moment the submodules are always mid-flight: their pyprojects are
    already bumped to the version being cut and their gitlinks will move again
    once they are tagged. Those changes say nothing about whether the root tree
    matches what the root gates ran against, which is what this check is for.
    Each submodule is checked in its own right by `tag_subpackages.py`.
    """
    args = ["status", "--porcelain", "--untracked-files=no"]
    if ignore_submodules:
        args.append("--ignore-submodules=all")
    _, out = git(args, cwd)
    return (out == ""), out


def submodule_paths(cwd: Path) -> set[str]:
    """Every submodule path registered in this superproject."""
    code, out = git(["submodule", "status"], cwd)
    if code != 0:
        return set()

    paths = set()
    for line in out.splitlines():
        # Each line is `<flag><sha> <path> (<describe>)`, where <flag> is a
        # single character -- a space when the submodule is in step.
        fields = line[1:].split()
        if len(fields) >= 2:
            paths.add(fields[1])
    return paths


def submodule_changes(cwd: Path) -> list[str]:
    """The submodule paths that currently have changes, for reporting only.

    `is_clean(..., ignore_submodules=True)` hides these from the release gate;
    printing them keeps the reason visible rather than silent.
    """
    subs = submodule_paths(cwd)
    _, out = git(["status", "--porcelain", "--untracked-files=no"], cwd)
    # A porcelain line is `XY <path>`. Split on whitespace rather than slicing a
    # fixed column: `git()` strips its output, which eats the leading space of
    # an unstaged first line and shifts every column by one.
    changed = [
        fields[1]
        for fields in (line.split(None, 1) for line in out.splitlines())
        if len(fields) == 2
    ]
    return [path for path in changed if path in subs]


def local_tag_exists(cwd: Path, tag: str) -> bool:
    code, _ = git(["rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"], cwd)
    return code == 0


def remote_tag_exists(cwd: Path, tag: str) -> bool:
    code, out = git(["ls-remote", "--tags", "origin", f"refs/tags/{tag}"], cwd)
    return code == 0 and bool(out)


def fetch(cwd: Path) -> tuple[int, str]:
    return git(["fetch", "--tags", "origin"], cwd)


def head_is_pushed(cwd: Path) -> tuple[bool, str]:
    """Whether HEAD already exists on origin.

    A tag on a commit the remote has never seen pushes a ref pointing at nothing
    the server can resolve, and the release would build from a commit nobody can
    check out.
    """
    branch = current_branch(cwd)
    code, _ = git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd)
    if code != 0:
        return False, f"origin/{branch} does not exist (branch never pushed)"

    code, _ = git(["merge-base", "--is-ancestor", "HEAD", f"origin/{branch}"], cwd)
    if code != 0:
        return False, f"HEAD is ahead of origin/{branch} -- push the branch first"
    return True, ""


def confirm(prompt: str) -> bool:
    """Ask before doing something that cannot be undone.

    Returns False on a non-interactive stdin rather than assuming yes, so an
    unattended invocation stops instead of publishing.
    """
    if not sys.stdin.isatty():
        print("stdin is not a terminal; re-run with --yes to confirm non-interactively")
        return False
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
