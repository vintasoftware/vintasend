import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_DIR = _REPO_ROOT / "templates" / "vintasend-implementation-template"
_CLONE_SCRIPT = _TEMPLATE_DIR / "scripts" / "clone.py"

_OLD_DISTRIBUTION_NAME = "vintasend-implementation-template"
_OLD_IMPORT_PACKAGE = "vintasend_implementation_template"

# Build/test artifacts the clone script must not copy. Checked in the test too, so a cache
# directory left over from running the template's own suite never gets asserted against.
_SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".git"}


class CloneScriptTestCase(TestCase):
    """Runs the real clone script against the real template source, into a throwaway
    directory, and checks the result is a clean, immediately-green, renamed package.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.target = Path(self._tmpdir.name) / "vintasend-clone-test"
        self.package_name = "vintasend-clone-test"
        self.import_package = "vintasend_clone_test"

    def _run_clone(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                str(_CLONE_SCRIPT),
                str(self.target),
                "--package-name",
                self.package_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def _iter_text_files(self):
        for path in self.target.rglob("*"):
            if not path.is_file() or any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            # scripts/clone.py legitimately names the template itself (docstring, SOURCE_*
            # constants) and is deliberately left unrewritten by the clone script -- skip it.
            if "scripts" in path.parts:
                continue
            try:
                yield path, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue

    def test_clone_script_exits_zero_and_prints_next_steps(self):
        result = self._run_clone()
        assert result.returncode == 0, result.stderr
        assert str(self.target) in result.stdout
        assert "poetry install" in result.stdout

    def test_clone_renames_the_package_directory(self):
        result = self._run_clone()
        assert result.returncode == 0, result.stderr

        assert not (self.target / _OLD_IMPORT_PACKAGE).exists()
        assert (self.target / self.import_package).is_dir()
        assert (self.target / self.import_package / "backend.py").is_file()

    def test_old_package_name_appears_nowhere_in_the_clone(self):
        result = self._run_clone()
        assert result.returncode == 0, result.stderr

        offenders = []
        for path, text in self._iter_text_files():
            if _OLD_DISTRIBUTION_NAME in text or _OLD_IMPORT_PACKAGE in text:
                offenders.append(str(path))
        assert not offenders, f"old package name still present in: {offenders}"

    def test_clone_does_not_edit_the_vintasend_dependency_pin(self):
        result = self._run_clone()
        assert result.returncode == 0, result.stderr

        source_pyproject = (_TEMPLATE_DIR / "pyproject.toml").read_text(encoding="utf-8")
        cloned_pyproject = (self.target / "pyproject.toml").read_text(encoding="utf-8")

        def dependency_line(text: str) -> str:
            for line in text.splitlines():
                if line.strip().startswith("vintasend ="):
                    return line.strip()
            raise AssertionError("no 'vintasend = ...' dependency line found")

        assert dependency_line(cloned_pyproject) == dependency_line(source_pyproject)

    def test_clone_produces_an_immediately_green_test_suite(self):
        result = self._run_clone()
        assert result.returncode == 0, result.stderr

        # The cloned package depends on `vintasend` and pytest, both already installed in this
        # environment. Rather than a fresh `poetry install` in the clone, put the clone on
        # sys.path via PYTHONPATH so its own pytest run resolves both the renamed package and
        # the `vintasend` it imports.
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{self.target}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else str(self.target)
        )

        pytest_result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=self.target,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert pytest_result.returncode == 0, pytest_result.stdout + pytest_result.stderr
        assert "passed" in pytest_result.stdout

    def test_clone_refuses_to_overwrite_an_existing_target(self):
        self.target.mkdir(parents=True)
        result = self._run_clone()
        assert result.returncode != 0
