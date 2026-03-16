"""Test stage: run pytest with coverage."""

from __future__ import annotations

from .runner import run_cmd


def run(project_dir: str = ".") -> bool:
    """Run pytest with coverage. Returns True on success."""
    return run_cmd(
        [
            "poetry",
            "run",
            "pytest",
            "--cov=github_runners_for_repo",
            "--cov-report=term-missing",
        ],
        cwd=project_dir,
    )
