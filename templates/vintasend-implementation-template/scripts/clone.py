#!/usr/bin/env python3
"""Clone this template into a new, renamed vintasend-* implementation package.

Usage:
    python scripts/clone.py /path/to/vintasend-my-integration
    python scripts/clone.py /path/to/vintasend-my-integration --package-name vintasend-my-integration

Copies this directory to the target path, then renames the package everywhere it appears:

- the distribution name in ``pyproject.toml`` (``vintasend-implementation-template`` -> your
  kebab-case name)
- the import package directory and every import of it (``vintasend_implementation_template`` ->
  your snake_case name)

It does NOT touch:

- the ``vintasend`` dependency pin in ``pyproject.toml``. Only the package's own name is
  renamed; the version range you depend on is yours to manage.
- the ``ImplementationTemplate*`` class name prefix inside the stub modules (for example
  ``ImplementationTemplateBackend``). Rename those yourself as you implement each seam --
  you'll usually want a name specific to your integration (``DjangoBackend``, not
  ``MyCompanyBackend``), and a global find-and-replace can't guess that for you.

Dependency-free: stdlib only.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


SOURCE_DISTRIBUTION_NAME = "vintasend-implementation-template"
SOURCE_IMPORT_PACKAGE = "vintasend_implementation_template"

# Never copy these into the clone -- they are build/test artifacts, not source.
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}

_KEBAB_NAME_RE = re.compile(r"[a-z][a-z0-9]*(-[a-z0-9]+)*")


def kebab_to_snake(name: str) -> str:
    """Convert a kebab-case distribution name to the snake_case import package name."""
    return name.replace("-", "_")


def validate_package_name(name: str) -> str:
    if not _KEBAB_NAME_RE.fullmatch(name):
        raise argparse.ArgumentTypeError(
            f"{name!r} is not a valid kebab-case distribution name "
            "(lowercase letters, digits, and single hyphens, e.g. 'vintasend-mycompany')"
        )
    return name


def _ignore_skipped_dirs(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in SKIP_DIR_NAMES}


def _rewrite_files_in_place(root: Path, old: str, new: str) -> None:
    """Replace every occurrence of ``old`` with ``new`` in every text file under ``root``."""
    if old == new:
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        # scripts/ (including this very clone.py) isn't part of the renamed package -- leave it alone.
        if "scripts" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Binary file (e.g. a stray .pyc that slipped past SKIP_DIR_NAMES) -- leave it alone.
            continue
        if old not in text:
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")


def clone(source: Path, target: Path, package_name: str) -> Path:
    """Copy ``source`` to ``target`` and rename the package to ``package_name`` throughout.

    Returns the path to the renamed import package directory inside ``target``.
    """
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")

    shutil.copytree(source, target, ignore=_ignore_skipped_dirs)

    import_package = kebab_to_snake(package_name)

    _rewrite_files_in_place(target, SOURCE_DISTRIBUTION_NAME, package_name)
    _rewrite_files_in_place(target, SOURCE_IMPORT_PACKAGE, import_package)

    old_package_dir = target / SOURCE_IMPORT_PACKAGE
    new_package_dir = target / import_package
    if old_package_dir.exists() and old_package_dir != new_package_dir:
        old_package_dir.rename(new_package_dir)

    return new_package_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone the vintasend implementation template into a new, renamed package."
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Destination directory for the new package. Must not already exist.",
    )
    parser.add_argument(
        "--package-name",
        type=validate_package_name,
        default=None,
        help=(
            "Kebab-case distribution name for the new package, e.g. 'vintasend-mycompany'. "
            "Defaults to the target directory's own name."
        ),
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Template directory to copy from. Defaults to this template.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    package_name = validate_package_name(args.package_name or args.target.name)

    clone(args.source, args.target, package_name)

    import_package = kebab_to_snake(package_name)
    print(f"Created {args.target} as '{package_name}' (import package: {import_package}).")
    print()
    print("Next steps:")
    print(f"  cd {args.target}")
    print("  poetry install")
    print("  poetry run pytest   # the scaffold tests should still pass, unchanged")
    print("  poetry run mypy")
    print()
    print("Then work through README.md's per-component checklist, replacing each TODO.")
    print(
        "Class names still start with 'ImplementationTemplate' -- rename them yourself as you "
        "implement each component."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
