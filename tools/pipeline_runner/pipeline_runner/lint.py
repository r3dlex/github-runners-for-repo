"""Lint stage: check formatting and lint rules."""

from __future__ import annotations

from .runner import run_cmd


def run(project_dir: str = ".") -> bool:
    """Run ruff format check and lint. Returns True on success."""
    if not run_cmd(["poetry", "run", "ruff", "format", "--check", "."], cwd=project_dir):
        return False
    if not run_cmd(["poetry", "run", "ruff", "check", "."], cwd=project_dir):
        return False
    return True
