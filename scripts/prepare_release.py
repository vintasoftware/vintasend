#!/usr/bin/env python3
"""Check the whole vintasend family against the unreleased local tree.

Every `vintasend-*` package depends on `vintasend` by version, so its test suite
normally runs against whatever is already on PyPI -- not against the code about
to be released. This script closes that gap: it repoints each package at the
local working copy, installs, and runs the gates, so a seam change that breaks a
downstream implementation is caught before the tag rather than after.

    1. rewrite every sibling dependency to a develop path dependency -- including
       one subpackage's dependency on another, such as
       vintasend-django-templates-manager on vintasend-managed-templates
    2. poetry lock && poetry install
    3. ruff check + ruff format
    4. mypy
    5. tox
    6. revert steps 1-2 (on by default; --keep leaves the tree linked)

The path dependencies written in step 1 must never be committed or published --
they point outside the package and would break the sdist. Step 6 is what keeps
that from happening, so the revert runs even when a gate fails; use --keep only
when you intend to debug in the linked state.

    scripts/prepare_release.py                     # full run, reverts at the end
    scripts/prepare_release.py --quick             # pytest instead of the tox matrix
    scripts/prepare_release.py --only vintasend-django
    scripts/prepare_release.py --keep              # leave the tree linked
    scripts/prepare_release.py --revert-only       # undo an earlier --keep run
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from _packages import (
    REPO_ROOT,
    TABLE_RE,
    Package,
    PackageError,
    find_packages,
    missing_submodules,
)


# Backups live in the tree, not a temp dir, so `--revert-only` still works in a
# later shell if a `--keep` run is abandoned.
BACKUP_DIR = REPO_ROOT / ".release-prep-backup"

# Full output of every failed step. The terminal only ever shows an excerpt, so
# this is where a failure that the excerpt clips badly stays readable.
LOG_DIR = REPO_ROOT / ".release-prep-logs"

# The files a run mutates. poetry.lock matters as much as pyproject.toml: an
# install against rewritten dependencies rewrites the lock, and a lock naming
# local paths is not something to commit either.
BACKED_UP = ("pyproject.toml", "poetry.lock")

# A single-line dependency on a sibling package, in any of the forms this repo
# uses: a bare constraint, an inline table, or an already-linked path.
SIBLING_DEP_RE = re.compile(r"^(?P<indent>\s*)(?P<name>vintasend[\w-]*)\s*=\s*(?P<value>.+?)\s*$")


class Status:
    OK = "ok"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    seconds: float = 0.0
    output: str = ""


@dataclass
class PackageRun:
    pkg: Package
    steps: list[StepResult] = field(default_factory=list)

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if s.status == Status.FAIL]


def subprocess_env() -> dict[str, str]:
    """The parent environment with any activated virtualenv detached.

    Poetry treats an activated virtualenv as the one to use, ahead of the
    per-project `.venv`. Launching this script as
    `poetry run python scripts/prepare_release.py` exports `VIRTUAL_ENV` pointing
    at the *root* venv, so every per-package `poetry install` and `poetry run`
    would silently target the root environment: subpackage dependencies get
    installed into the root venv, and each package's gates run against the wrong
    interpreter. Dropping the variable restores per-package resolution.

    Only `VIRTUAL_ENV` has this effect -- `PATH` and `POETRY_ACTIVE` do not, but
    `POETRY_ACTIVE` is dropped too so the child sees a coherent environment.
    """
    env = os.environ.copy()
    for name in ("VIRTUAL_ENV", "POETRY_ACTIVE"):
        env.pop(name, None)
    return env


def run(
    cmd: list[str], cwd: Path, timeout: int = 3600, label: str | None = None
) -> tuple[int, str]:
    """Run a command, returning its exit code and combined output.

    Output is captured rather than streamed, so a caller that passes `label`
    announces the command first. Without that a slow step -- the tox matrix
    rebuilds a venv per interpreter and can run for tens of minutes -- looks
    indistinguishable from a hang.
    """
    if label:
        print(f"  ...  {label}", flush=True)
    try:
        # noqa S603: every argv here is built from this module's own literals and
        # discovered package paths, never from user input, and shell=False.
        proc = subprocess.run(  # noqa: S603
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=subprocess_env(),
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------
# step 1 -- repoint sibling dependencies at the local tree
# --------------------------------------------------------------------------


def relative_path(from_dir: Path, to_dir: Path) -> str:
    """A POSIX relative path from one package directory to another."""
    return os.path.relpath(to_dir, from_dir).replace("\\", "/")


def link_siblings(pkg: Package, by_name: dict[str, Package]) -> tuple[list[str], list[str]]:
    """Rewrite sibling deps to develop path deps.

    Every dependency on another package in this superproject is linked, not just
    the one on the root: `vintasend-django-templates-manager` depends on
    `vintasend-managed-templates` as well, and testing the first against a
    released copy of the second defeats the point of this script.

    Only lines inside a `*dependencies` table are touched, and only when the key
    names another package here -- so an unrelated key that merely starts with
    "vintasend" in some tool's config is left alone.

    Returns the edits made, and separately the deps that were *already* path
    dependencies before this ran. That second list should always be empty: it
    means the committed tree ships a dependency on a directory, which no
    installed package can resolve. Reverting will faithfully restore it, so the
    caller reports it rather than quietly fixing it.
    """
    lines = pkg.path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    table = ""
    edits: list[str] = []
    already: list[str] = []

    for line in lines:
        header = TABLE_RE.match(line.rstrip("\n"))
        if header:
            table = header.group(1).strip()
            out.append(line)
            continue

        match = SIBLING_DEP_RE.match(line.rstrip("\n")) if table.endswith("dependencies") else None
        target = by_name.get(match.group("name")) if match else None
        if match is None or target is None or target.dir == pkg.dir:
            out.append(line)
            continue

        rel = relative_path(pkg.dir, target.dir)
        replacement = (
            f'{match.group("indent")}{match.group("name")} = {{ path = "{rel}", develop = true }}\n'
        )
        if replacement.strip() == line.strip():
            already.append(match.group("name"))
            out.append(line)
            continue

        edits.append(f'{match.group("name")}: {match.group("value")} -> path "{rel}"')
        out.append(replacement)

    if edits:
        pkg.path.write_text("".join(out), encoding="utf-8")
    return edits, already


# --------------------------------------------------------------------------
# backup / restore
# --------------------------------------------------------------------------


def take_backup(packages: list[Package]) -> None:
    if BACKUP_DIR.exists():
        raise PackageError(
            f"{BACKUP_DIR.relative_to(REPO_ROOT)} already exists -- an earlier --keep run was "
            "never reverted.\nRun `scripts/prepare_release.py --revert-only` first, or delete "
            "that directory if you know the tree is clean."
        )
    for pkg in packages:
        dest = BACKUP_DIR / pkg.rel
        dest.mkdir(parents=True, exist_ok=True)
        for filename in BACKED_UP:
            source = pkg.dir / filename
            if source.exists():
                shutil.copy2(source, dest / filename)


def restore_backup(quiet: bool = False) -> int:
    """Put every backed-up file back and drop the backup directory."""
    if not BACKUP_DIR.exists():
        if not quiet:
            print("nothing to revert: no backup directory")
        return 0

    restored = 0
    for saved in sorted(BACKUP_DIR.rglob("*")):
        if not saved.is_file():
            continue
        target = REPO_ROOT / saved.relative_to(BACKUP_DIR)
        shutil.copy2(saved, target)
        restored += 1

    shutil.rmtree(BACKUP_DIR)
    if not quiet:
        print(f"reverted {restored} file(s); removed {BACKUP_DIR.relative_to(REPO_ROOT)}")
    return restored


def reinstall_after_revert(packages: list[Package]) -> None:
    """Re-lock and re-install so the venvs match the restored pyproject files.

    Skipped silently where it cannot succeed: once the deps point back at
    released versions, a package pinning an unpublished vintasend has nothing to
    resolve against.
    """
    for pkg in packages:
        code, _ = run(["poetry", "lock"], cwd=pkg.dir, timeout=600)
        if code != 0:
            print(f"  {pkg.name}: could not re-lock; run `poetry lock` there once released")
            continue
        run(["poetry", "install"], cwd=pkg.dir, timeout=1800)


# --------------------------------------------------------------------------
# steps 2-5 -- install and gates
# --------------------------------------------------------------------------


def has_dev_tool(pkg: Package, tool: str) -> bool:
    """Whether `tool` is declared as a dependency of this package."""
    text = pkg.path.read_text(encoding="utf-8")
    return re.search(rf"^\s*{re.escape(tool)}\s*=", text, re.MULTILINE) is not None


def step_install(pkg: Package) -> list[StepResult]:
    results = []
    start = time.monotonic()
    code, output = run(["poetry", "lock"], cwd=pkg.dir, timeout=900, label="poetry lock")
    results.append(
        StepResult(
            "poetry lock",
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    )
    if code != 0:
        return results

    start = time.monotonic()
    code, output = run(["poetry", "install"], cwd=pkg.dir, timeout=1800, label="poetry install")
    results.append(
        StepResult(
            "poetry install",
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    )
    return results


def step_ruff(pkg: Package, fix: bool) -> list[StepResult]:
    """Lint and format-check.

    ruff is a standalone linter that imports nothing from the project, so where a
    package does not declare it we fall back to the root's copy. Run with the
    package as cwd it still discovers that package's own [tool.ruff] config.
    """
    if has_dev_tool(pkg, "ruff"):
        base = ["poetry", "run", "ruff"]
        via = ""
    else:
        root_ruff = REPO_ROOT / ".venv" / "bin" / "ruff"
        if not root_ruff.exists():
            return [StepResult("ruff", Status.SKIP, "ruff not available in this package or root")]
        base = [str(root_ruff)]
        via = " (via root ruff)"

    results = []
    check_cmd = [*base, "check", "."] + (["--fix"] if fix else [])
    start = time.monotonic()
    code, output = run(check_cmd, cwd=pkg.dir, timeout=600, label=f"ruff check{via}")
    results.append(
        StepResult(
            f"ruff check{via}",
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    )

    fmt_cmd = [*base, "format", "."] + ([] if fix else ["--check"])
    start = time.monotonic()
    code, output = run(fmt_cmd, cwd=pkg.dir, timeout=600, label=f"ruff format{via}")
    results.append(
        StepResult(
            f"ruff format{'' if fix else ' --check'}{via}",
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    )
    return results


def step_mypy(pkg: Package) -> list[StepResult]:
    """Type-check.

    No fallback here, unlike ruff: mypy resolves the package's own imports, so
    running the root's copy against a package whose dependencies are not in that
    environment reports errors that are artefacts of the wrong environment.
    """
    if not has_dev_tool(pkg, "mypy"):
        return [StepResult("mypy", Status.SKIP, "mypy is not a dependency of this package")]

    start = time.monotonic()
    code, output = run(["poetry", "run", "mypy"], cwd=pkg.dir, timeout=1800, label="mypy")
    return [
        StepResult(
            "mypy",
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    ]


def step_tests(pkg: Package, quick: bool) -> list[StepResult]:
    """The tox matrix, or pytest where there is no tox.ini."""
    has_tox = (pkg.dir / "tox.ini").exists() and has_dev_tool(pkg, "tox")
    if quick or not has_tox:
        why = "--quick" if quick else "no tox.ini"
        cmd, label = ["poetry", "run", "pytest"], f"pytest ({why})"
    else:
        cmd, label = ["poetry", "run", "tox"], "tox"

    start = time.monotonic()
    code, output = run(cmd, cwd=pkg.dir, timeout=3600, label=f"{label} (this is the slow one)")
    return [
        StepResult(
            label,
            Status.OK if code == 0 else Status.FAIL,
            seconds=time.monotonic() - start,
            output=output,
        )
    ]


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def tail(text: str, lines: int = 15) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()][-lines:]
    return "\n".join(f"        {line}" for line in kept)


# Where pytest's own report begins. A tox run ends with its per-env summary
# ("py312: FAIL code 1", "evaluation failed :("), so the tail of the output says
# only *that* something failed -- the assertions are hundreds of lines earlier.
# FAILURES/ERRORS carry the tracebacks and assertion diffs; the short summary is
# the one-line-per-failure list that closes the report.
DETAIL_MARKERS = ("=== FAILURES", "=== ERRORS")
SUMMARY_MARKER = "=== short test summary info"


def _banner_start(text: str, marker: str) -> int:
    """Index of the start of the banner line `marker` appears on, or -1."""
    index = text.find(marker)
    return -1 if index == -1 else text.rfind("\n", 0, index) + 1


def indent(block: str) -> str:
    return "\n".join(f"        {line}" for line in block.splitlines())


def excerpt(text: str, lines: int = 40) -> str:
    """The most useful slice of a failed command's output.

    Shows pytest's traceback dump (capped, since one run can produce hundreds of
    lines) and then always appends the short summary in full, so a truncated
    traceback never hides *which* tests failed. Falls back to a plain tail for
    output that is not a pytest report at all -- a failed `poetry install`, say.
    """
    stripped = text.strip()
    if not stripped:
        return "        (no output)"

    detail_at = next(
        (i for i in (_banner_start(stripped, m) for m in DETAIL_MARKERS) if i != -1), -1
    )
    summary_at = _banner_start(stripped, SUMMARY_MARKER)

    if detail_at == -1 and summary_at == -1:
        return tail(text)

    parts = []
    if detail_at != -1:
        end = summary_at if summary_at > detail_at else len(stripped)
        block = stripped[detail_at:end].strip().splitlines()
        parts.append(indent("\n".join(block[:lines])))
        if len(block) > lines:
            parts.append(f"        ... {len(block) - lines} more line(s) -- see the log")
    if summary_at != -1:
        parts.append(indent(stripped[summary_at:].strip()))

    return "\n".join(parts)


def write_log(pkg_name: str, step_name: str, output: str) -> Path:
    """Persist a failed step's full output and return the path.

    The excerpt above is a guess at what matters; the log is the whole thing, so
    a failure that the heuristic slices badly is still recoverable.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^\w.-]+", "-", f"{pkg_name}__{step_name}").strip("-")
    path = LOG_DIR / f"{slug}.log"
    path.write_text(output, encoding="utf-8")
    return path


def report(runs: list[PackageRun], linked: bool) -> int:
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    any_failed = False
    skipped: list[str] = []
    for prun in runs:
        marks = []
        for step in prun.steps:
            symbol = {Status.OK: "ok", Status.FAIL: "FAIL", Status.SKIP: "skip"}[step.status]
            marks.append(f"{step.name}={symbol}")
            if step.status == Status.SKIP:
                skipped.append(f"{prun.pkg.name}: {step.name} -- {step.detail}")
        state = "FAIL" if prun.failed else "ok"
        any_failed = any_failed or bool(prun.failed)
        print(f"\n  [{state:>4}] {prun.pkg.name}")
        print(f"         {'  '.join(marks)}")

    if skipped:
        print("\n" + "-" * 78)
        print("SKIPPED -- these gates could not run, and are not evidence of passing:")
        for note in skipped:
            print(f"  {note}")

    failures = [(p, s) for p in runs for s in p.failed]
    if failures:
        print("\n" + "-" * 78)
        print("FAILURES")
        for prun, step in failures:
            log = write_log(prun.pkg.name, step.name, step.output)
            print(f"\n  {prun.pkg.name} -> {step.name}")
            print(excerpt(step.output))
            print(f"\n        full output: {log.relative_to(REPO_ROOT)}")

    print("\n" + "=" * 78)
    if linked:
        print("!! pyproject.toml files still point at local paths (--keep).")
        print("!! Do not commit or publish in this state.")
        print("!! Undo with: scripts/prepare_release.py --revert-only")
    print("RESULT:", "FAILED" if any_failed else "all gates passed")
    return 1 if any_failed else 0


# --------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Link every vintasend package at the local tree and run the release gates.",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave the develop path dependencies in place instead of reverting",
    )
    parser.add_argument(
        "--revert-only",
        action="store_true",
        help="restore pyproject.toml/poetry.lock from a previous --keep run and exit",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run pytest on the current interpreter instead of the full tox matrix",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="let ruff apply its fixes and reformat, instead of only reporting",
    )
    parser.add_argument(
        "--only",
        metavar="NAME",
        action="append",
        help="restrict the run to this package (repeatable); root is always installed",
    )
    args = parser.parse_args()

    if args.revert_only:
        restore_backup()
        return 0

    try:
        missing = missing_submodules()
        if missing:
            raise PackageError(
                "these submodules are not checked out: "
                + ", ".join(missing)
                + "\nrun `git submodule update --init` first"
            )
        packages = find_packages()
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    by_name = {p.name: p for p in packages}
    selected = packages
    if args.only:
        wanted = set(args.only)
        unknown = wanted - set(by_name)
        if unknown:
            print(f"error: unknown package(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            print(f"known: {', '.join(sorted(by_name))}", file=sys.stderr)
            return 1
        selected = [p for p in packages if p.name in wanted]

    # ---- step 1 -------------------------------------------------------
    try:
        take_backup(packages)
    except PackageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("=" * 78)
    print("STEP 1  link sibling dependencies at the local tree")
    print("=" * 78)
    shipped_local: dict[str, list[str]] = {}
    for pkg in packages:
        edits, already = link_siblings(pkg, by_name)
        if already:
            shipped_local[pkg.name] = already
        if edits:
            print(f"\n  {pkg.name}")
            for edit in edits:
                print(f"    {edit}")
    print(f"\n  backup written to {BACKUP_DIR.relative_to(REPO_ROOT)}/")

    if shipped_local:
        print(
            "\n  WARNING: these packages already had a path dependency before this run, so it is\n"
            "  what they ship. A published package cannot resolve a directory, and the release\n"
            "  scripts will refuse to lock or tag them until each one is a version pin again:"
        )
        for name, deps in shipped_local.items():
            print(f"    {name}: {', '.join(deps)}")
        print(
            "  reverting restores exactly this state -- it is a committed edit to undo, not a\n"
            "  leftover from an earlier --keep run."
        )

    # Drop logs from an earlier run: a stale file next to this run's failures
    # reads as though it belongs to this one.
    if LOG_DIR.exists():
        shutil.rmtree(LOG_DIR)

    if not args.quick:
        # tox builds a fresh venv per interpreter per package and runs
        # `poetry install --with dev` in each, so the full sweep is measured in
        # hours, not minutes. Worth knowing before staring at a quiet terminal.
        print(
            f"\n  running the full tox matrix across {len(selected)} package(s). Each package "
            "rebuilds\n  one venv per supported interpreter, so expect this to take a long time.\n"
            "  Use --quick to run pytest on the current interpreter instead."
        )

    runs: list[PackageRun] = []
    exit_code = 1
    try:
        for pkg in selected:
            print("\n" + "=" * 78)
            print(f"{pkg.name}  ({pkg.rel})")
            print("=" * 78)
            prun = PackageRun(pkg=pkg)

            def record(steps: list[StepResult], prun: PackageRun = prun) -> None:
                """Add steps to the run and report each one as it lands.

                Printed here rather than once per package: the tox matrix can run
                for tens of minutes, and batching the report until after it means
                the gates that already passed stay invisible until then.
                """
                for step in steps:
                    symbol = {Status.OK: "ok  ", Status.FAIL: "FAIL", Status.SKIP: "skip"}[
                        step.status
                    ]
                    suffix = f"  ({step.seconds:.0f}s)" if step.seconds else ""
                    print(f"  [{symbol}] {step.name}{suffix}", flush=True)
                prun.steps += steps

            # ---- step 2 -----------------------------------------------
            record(step_install(pkg))
            if prun.failed:
                # Nothing downstream is meaningful without an environment.
                print("  install failed; skipping the gates for this package")
                runs.append(prun)
                continue

            # ---- steps 3-5 --------------------------------------------
            record(step_ruff(pkg, fix=args.fix))
            record(step_mypy(pkg))
            record(step_tests(pkg, quick=args.quick))
            runs.append(prun)

        exit_code = report(runs, linked=args.keep)
    except KeyboardInterrupt:
        # The revert below still runs, which is the point: an interrupted run
        # must not leave path dependencies in the tree.
        print("\n\ninterrupted -- reverting before exit")
    finally:
        # ---- step 6 ---------------------------------------------------
        # Runs even on failure or Ctrl-C: leaving path dependencies behind is
        # how a broken sdist reaches PyPI.
        if not args.keep:
            print("\n" + "=" * 78)
            print("STEP 6  revert")
            print("=" * 78)
            restore_backup()
            reinstall_after_revert(selected)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
