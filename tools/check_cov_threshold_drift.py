"""Drift guard for the coverage threshold.

Reads `[tool.coverage.report] fail_under` from `pyproject.toml` and the
`--cov-fail-under=N` flag from `.github/workflows/coverage.yml`. Exits
0 when the two values match and 1 when they diverge.

This is the dual-enforcement point for AC-22: even if a developer
updates one half and forgets the other, this script catches the drift
in pre-commit and in the `coverage` CI job.

Usage:
    uv run python tools/check_cov_threshold_drift.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
COVERAGE_YML = REPO_ROOT / ".github" / "workflows" / "coverage.yml"

PYPROJECT_FAIL_UNDER_RE = re.compile(
    r"\[tool\.coverage\.report\][^\[]*?fail_under\s*=\s*(\d+)"
)
WORKFLOW_COV_FAIL_UNDER_RE = re.compile(r"--cov-fail-under[= ](\d+)")


def read_pyproject_fail_under() -> int | None:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = PYPROJECT_FAIL_UNDER_RE.search(text)
    return int(m.group(1)) if m else None


def read_workflow_fail_under() -> int | None:
    if not COVERAGE_YML.exists():
        return None
    text = COVERAGE_YML.read_text(encoding="utf-8")
    m = WORKFLOW_COV_FAIL_UNDER_RE.search(text)
    return int(m.group(1)) if m else None


def main() -> int:
    py = read_pyproject_fail_under()
    wf = read_workflow_fail_under()
    if py is None:
        print(
            f"pyproject.toml: [tool.coverage.report] fail_under not found",
            file=sys.stderr,
        )
    if wf is None:
        print(
            f"{COVERAGE_YML.relative_to(REPO_ROOT)}: --cov-fail-under not found",
            file=sys.stderr,
        )
    if py is None or wf is None:
        return 1
    if py != wf:
        print(
            f"Coverage threshold drift: pyproject.toml says {py}, "
            f"coverage workflow says {wf}. Update both to the same value.",
            file=sys.stderr,
        )
        return 1
    print(f"Coverage threshold OK: {py} in both pyproject.toml and the coverage workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
