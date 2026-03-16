"""Coverage stage: run tests with coverage and enforce minimum threshold."""

from __future__ import annotations

from .runner import run_cmd


DEFAULT_THRESHOLD = 75


def run(project_dir: str = ".", threshold: int = DEFAULT_THRESHOLD) -> bool:
    """Run pytest with coverage and fail if below threshold. Returns True on success."""
    return run_cmd(
        [
            "poetry",
            "run",
            "pytest",
            "--cov=github_runners_for_repo",
            "--cov-report=term-missing",
            f"--cov-fail-under={threshold}",
        ],
        cwd=project_dir,
    )
