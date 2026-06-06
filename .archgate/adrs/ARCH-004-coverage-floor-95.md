---
id: ARCH-004
title: 95% coverage floor on github_runners_for_repo
domain: quality
rules: true
files: ["pyproject.toml", ".github/workflows/coverage.yml"]
---

# ARCH-004 — 95% coverage floor on github_runners_for_repo

## Context

The project must ship with test coverage on its main code path. A 95%
line+branch floor on `github_runners_for_repo/` is the chosen gate —
high enough to catch regressions in the runner lifecycle, low enough
that 4-file packages can meet it without ceremony.

The drift between the project's `pyproject.toml` and the CI flag is a
known footgun (the `coverage` workflow can be edited without updating
the floor). The drift is guarded by `tools/check_cov_threshold_drift.py`
(AC-22), which runs in pre-commit and in the `coverage` workflow.

## Decision

1. **Floor = 95%** for the `github_runners_for_repo/` package, measured
   by `pytest --cov=github_runners_for_repo`.
2. **Two enforcement points.**
   - `pyproject.toml` `[tool.coverage.report] fail_under = 95`.
   - `coverage` GH Actions workflow uses `--cov-fail-under=95`.
3. **Drift guard.** `tools/check_cov_threshold_drift.py` fails if the
   two values diverge. Runs in pre-commit (`pre-commit` stage) and as
   a CI step in the `coverage` workflow.

The companion rule (`coverage-floor-asserted`) reads
`[tool.coverage.report] fail_under` from `pyproject.toml` and asserts
it equals 95.

## Do's and Don'ts

**Do:**

- Add branch tests when fixing a bug — the floor enforces it.
- Run `uv run python tools/check_cov_threshold_drift.py` after
  editing `pyproject.toml` or the `coverage` workflow.

**Don't:**

- Don't lower the floor to make a refactor merge — fix the coverage
  instead.
- Don't exclude lines with `# pragma: no cover` to dodge the floor
  unless there is a justified reason in a comment on the same line.

## Implementation Pattern

Good example — `pyproject.toml`:

```toml
[tool.coverage.report]
fail_under = 95
```

Good example — `.github/workflows/coverage.yml`:

```yaml
- run: uv run pytest --cov=github_runners_for_repo --cov-report=xml --cov-fail-under=95
```

Bad example — diverged values:

```toml
# pyproject.toml
[tool.coverage.report]
fail_under = 95
```
```yaml
# .github/workflows/coverage.yml
- run: uv run pytest --cov=github_runners_for_repo --cov-fail-under=80
```

## Consequences

**Positive:** every merge is verified to maintain or improve coverage.
The drift guard catches the case where one half is updated and the
other is forgotten.

**Negative:** refactors that change the public API may need
accompanying test additions. This is the intended friction.

**Risks:** branch coverage is harder to game than line coverage;
`pytest-cov` reports both by default with `--cov-branch`. Mitigated
by the explicit branch flag in the CI step.

## Compliance and Enforcement

**Automated:**

- `.archgate/adrs/ARCH-004-coverage-floor-95.rules.ts` — reads
  `pyproject.toml` and asserts `fail_under == 95`.
- `tools/check_cov_threshold_drift.py` (AC-22) — fails on drift.
- `coverage` workflow — `--cov-fail-under=95`.

**Manual:**

- A reviewer flags a PR that adds a `# pragma: no cover` without a
  justification comment.

## References

- AC-8 (the fresh-clone dev loop)
- AC-22 (the drift guard)
- `tools/check_cov_threshold_drift.py`
