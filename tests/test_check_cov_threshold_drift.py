"""Unit tests for tools/check_cov_threshold_drift.py (AC-22)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# tools/ is a directory of scripts, not a Python package, so we import
# the module by file path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import check_cov_threshold_drift as drift  # noqa: E402


@pytest.fixture
def reload_drift(monkeypatch, tmp_path):
    """Rebind the module's REPO_ROOT to a tmp directory and reload it."""

    def _make(pyproject_text: str | None, workflow_text: str | None):
        repo = tmp_path
        (repo / "tools").mkdir(parents=True, exist_ok=True)
        if pyproject_text is not None:
            (repo / "pyproject.toml").write_text(pyproject_text, encoding="utf-8")
        if workflow_text is not None:
            wf = repo / ".github" / "workflows"
            wf.mkdir(parents=True, exist_ok=True)
            (wf / "coverage.yml").write_text(workflow_text, encoding="utf-8")
        # Re-bind the module-level path constants.
        monkeypatch.setattr(drift, "REPO_ROOT", repo)
        monkeypatch.setattr(drift, "PYPROJECT", repo / "pyproject.toml")
        monkeypatch.setattr(drift, "COVERAGE_YML", repo / ".github" / "workflows" / "coverage.yml")
        return repo

    return _make


def test_clean_match_passes(reload_drift, capsys):
    reload_drift(
        "[tool.coverage.report]\nfail_under = 95\n",
        "- run: uv run pytest --cov=github_runners_for_repo --cov-fail-under=95\n",
    )
    assert drift.main() == 0


def test_drifted_pyproject_fails(reload_drift):
    reload_drift(
        "[tool.coverage.report]\nfail_under = 95\n",
        "- run: uv run pytest --cov=github_runners_for_repo --cov-fail-under=80\n",
    )
    assert drift.main() == 1


def test_drifted_workflow_fails(reload_drift):
    reload_drift(
        "[tool.coverage.report]\nfail_under = 80\n",
        "- run: uv run pytest --cov=github_runners_for_repo --cov-fail-under=95\n",
    )
    assert drift.main() == 1


def test_missing_pyproject_section_fails(reload_drift):
    reload_drift(
        "[tool.ruff]\nline-length = 100\n",
        "- run: uv run pytest --cov-fail-under=95\n",
    )
    assert drift.main() == 1


def test_missing_workflow_flag_fails(reload_drift):
    reload_drift(
        "[tool.coverage.report]\nfail_under = 95\n",
        "- run: uv run pytest --cov=github_runners_for_repo\n",
    )
    assert drift.main() == 1
