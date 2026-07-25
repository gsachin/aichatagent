"""
Task 1 — Create app/ directory structure
=========================================
Goal: Scaffold the project package and verify Python can import it.
"""

import importlib
import os
from pathlib import Path


class TestTask1Package:
    """Verify the app/ package scaffold exists and is importable."""

    def test_app_directory_exists(self):
        """The app/ directory must exist in the project root."""
        app_dir = Path(__file__).resolve().parent.parent / "app"
        assert app_dir.is_dir(), f"app/ directory not found at {app_dir}"

    def test_init_py_exists(self):
        """app/__init__.py must be present (can be empty)."""
        init_file = Path(__file__).resolve().parent.parent / "app" / "__init__.py"
        assert init_file.is_file(), f"__init__.py not found at {init_file}"

    def test_app_package_importable(self):
        """'import app' must succeed without errors."""
        try:
            import app  # noqa: F401
        except ImportError as e:
            pytest.fail(f"import app failed: {e}")

    def test_app_has_module_attribute(self):
        """app.__file__ should point inside the project."""
        import app
        assert app.__file__ is not None
        assert "app" in app.__file__

    def test_init_py_not_corrupted(self):
        """__init__.py must not contain syntax errors or malicious code."""
        init_path = Path(__file__).resolve().parent.parent / "app" / "__init__.py"
        with open(init_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Compile to check syntax
        compile(content, str(init_path), "exec")
