# CI Pipelines

## Overview

Seven GitHub Actions workflows run on every push to `main` and on every pull request. All are zero-install: they use `astral-sh/setup-uv@v4` + `uv sync --frozen` + `uv run <tool>` — no Poetry, no custom pipeline-runner library, no pre-configured runners or external services required.

The same `uv run` commands run locally via pre-commit (see `.pre-commit-config.yaml` and `AGENTS.md`) and in CI. Local and CI parity is enforced: if a check is green on a maintainer's machine, it will be green in CI.

## Workflows

### Lint (`lint.yml`)

Checks code formatting and style with Ruff.

- **Runs:** `uv run ruff check .` + `uv run ruff format --check .`
- **Python:** 3.11
- **Fails on:** formatting drift or lint violations

### Test (`test.yml`)

Runs the full test suite across Python versions.

- **Runs:** `uv run pytest`
- **Matrix:** Python 3.11, 3.12, 3.13
- **Fails on:** any test failure

### Coverage (`coverage.yml`)

Enforces a minimum 95% test coverage threshold plus the AC-22 drift guard.

- **Runs:** `uv run pytest --cov=github_runners_for_repo --cov-fail-under=95` + `uv run python tools/check_cov_threshold_drift.py`
- **Python:** 3.11
- **Fails on:** coverage below 95% or threshold drift between `pyproject.toml` and the workflow

### Build (`build.yml`)

Builds the wheel and verifies the entry point exists in the build output.

- **Runs:** `uv build` + entry-point verification
- **Python:** 3.11
- **Artifacts:** uploads `dist/` (sdist + wheel)
- **Fails on:** build error or missing entry point

### archgate (`archgate.yml`)

Runs `archgate check` against the 5 ADRs at `.archgate/adrs/`.

- **Runs:** `archgate check` (uses `archgate/setup-action@v1`; archgate v0.43+ ships a bundled TS runtime)
- **Fails on:** any ADR rule violation

### pr-issue-link (`pr-issue-link.yml`)

Enforces the AC-21 PR↔Issue linkage rule.

- **Runs:** `python tools/check_pr_link.py --pr-body "$PR_BODY"`
- **Fails on:** PR body lacks `Closes #N` / `Fixes #N` / `Resolves #N` to an open issue

### ty (`ty.yml`)

Astral's type checker.

- **Runs:** `uv run ty check github_runners_for_repo`
- **Fails on:** any type error

### deptry-pip-audit (`deptry-pip-audit.yml`)

Dependency hygiene: deptry catches unused/undeclared dependencies, pip-audit catches known CVEs.

- **Runs:** `uv run deptry .` + `uv run pip-audit`
- **Fails on:** any dependency issue or known vulnerability

## Design Decisions

**Why `astral-sh/setup-uv@v4`?**
It's the upstream first-party action for `uv`. It caches `~/.cache/uv` keyed on `uv.lock`, which keeps cold CI runs fast and reproducible.

**Why `uv sync --frozen` in CI?**
`--frozen` means "do not modify the lockfile" — it asserts the committed `uv.lock` is in sync with `pyproject.toml`. If they drift, the install fails fast.

**Why seven workflows and not one?**
Each concern (style, correctness, packaging, types, dependencies, governance, PR hygiene) has a distinct failure mode and fix path. Splitting them gives faster feedback — a lint failure doesn't block seeing test results — and lets branch protection require each as a separate gate.

**Why matrix only on tests?**
Lint rules, type checks, and build output don't vary across Python versions. Tests do — different runtime behavior, stdlib changes, and dependency compatibility.

**Why verify the wheel's entry point?**
A package that builds but can't be installed or run is a silent failure. The build workflow extracts the wheel and confirms `cli.py` is present and importable, catching missing entry points, broken imports, and packaging issues.

**Why a separate coverage workflow?**
The test workflow runs across a Python version matrix to catch compatibility issues. Coverage only needs to run once (on 3.11) and enforces a threshold gate — mixing them would either duplicate the gate or skip multi-version testing.

**Why deptry *and* pip-audit?**
deptry catches structural issues (unused, undeclared, or missing-from-optional-dependencies) that pip-audit does not. pip-audit catches known CVEs that deptry does not. Both are required to cover the dependency-hygiene surface.

**Why archgate as a workflow?**
archgate's `.rules.ts` files encode the ADRs as executable checks. Running `archgate check` in CI turns the ADRs from documentation into enforcement. A reviewer who tries to bypass a rule fails the build.
