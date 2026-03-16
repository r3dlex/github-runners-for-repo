# CI Pipelines

## Overview

Three GitHub Actions workflows run on every push to `main` and on pull requests. All are zero-install: they use `actions/setup-python` and `pipx install poetry` — no pre-configured runners or external services required.

## Workflows

### Lint (`lint.yml`)

Checks code formatting and style.

- **Runs:** `ruff format --check .` then `ruff check .`
- **Python:** 3.11
- **Fails on:** formatting drift or lint violations

### Test (`test.yml`)

Runs the full test suite across Python versions.

- **Runs:** `pytest --cov` with coverage report
- **Matrix:** Python 3.11, 3.12, 3.13
- **Fails on:** any test failure

### Build (`build.yml`)

Builds and validates the distributable package.

- **Runs:** `poetry build`, then installs the wheel and runs `gh-runners --help`
- **Artifacts:** uploads `dist/` (sdist + wheel)
- **Fails on:** build error or broken entry point

## Design Decisions

**Why three separate workflows?**
Each concern (style, correctness, packaging) has a distinct failure mode and fix path. Splitting them gives faster feedback — a lint failure doesn't block seeing test results.

**Why `pipx install poetry`?**
GitHub-hosted runners include `pipx` by default. This avoids curl-based installer scripts and pinning installer versions.

**Why matrix only on tests?**
Lint rules and build output don't vary across Python versions. Testing does — different runtime behavior, stdlib changes, and dependency compatibility.

**Why verify the wheel?**
A package that builds but can't be installed or run is a silent failure. Installing the wheel and running `--help` catches missing entry points, broken imports, and packaging issues.
