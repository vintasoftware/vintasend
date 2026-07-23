import sys
from pathlib import Path
from unittest import TestCase


if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


_ROOT_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class TemplateExclusionTestCase(TestCase):
    """`templates/` holds the in-repo implementation-template package. It has its own
    `pyproject.toml`, its own ruff config, and is never meant to be linted, type-checked,
    or test-collected by this repo's own tooling -- the same way `implementations/` (the
    downstream submodules) is already excluded.

    These assertions read the ROOT `pyproject.toml` directly, so a future change that
    accidentally widens ruff's scope, mypy's `files`, or pytest's `testpaths` back onto
    `templates/` fails loudly here instead of silently linting/type-checking/collecting
    a package this repo does not release.
    """

    def setUp(self):
        with open(_ROOT_PYPROJECT, "rb") as f:
            self.config = tomllib.load(f)

    def test_ruff_extend_exclude_lists_templates(self):
        extend_exclude = self.config["tool"]["ruff"]["extend-exclude"]
        assert "templates" in extend_exclude

    def test_ruff_extend_exclude_still_lists_implementations(self):
        # Guards against the new entry accidentally replacing rather than joining
        # the existing exclusion.
        extend_exclude = self.config["tool"]["ruff"]["extend-exclude"]
        assert "implementations" in extend_exclude

    def test_mypy_files_is_scoped_to_vintasend_only(self):
        files = self.config["tool"]["mypy"]["files"]
        assert files == ["vintasend"]

    def test_pytest_testpaths_is_scoped_to_vintasend_only(self):
        testpaths = self.config["tool"]["pytest"]["ini_options"]["testpaths"]
        assert testpaths == ["vintasend"]
