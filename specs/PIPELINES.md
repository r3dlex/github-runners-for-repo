# CI Pipelines

## Overview

Three GitHub Actions workflows run on every push to `main` and on pull requests. All are zero-install: they use `actions/setup-python`, `pipx install poetry`, and `pip install ./tools/pipeline_runner` — no pre-configured runners or external services required.

Pipeline logic lives in `tools/pipeline_runner/`, a standalone Python package that the workflows install and invoke via the `pipeline-runner` CLI.

## Pipeline Runner (`tools/pipeline_runner/`)

A lightweight CLI that wraps CI stages into repeatable commands:

| Command | What it runs |
|---|---|
| `pipeline-runner lint` | `ruff format --check .` then `ruff check .` |
| `pipeline-runner test` | `pytest --cov` with coverage report |
| `pipeline-runner build` | `poetry build`, install wheel, verify `gh-runners --help` |
| `pipeline-runner all` | All three stages in sequence, fails fast |

### Local usage

```bash
pip install ./tools/pipeline_runner
pipeline-runner lint
pipeline-runner test
pipeline-runner all
```

### Structure

```
tools/pipeline_runner/
├── pyproject.toml              # Poetry package definition
└── pipeline_runner/
    ├── __init__.py
    ├── cli.py                  # CLI entry point and stage dispatch
    ├── runner.py               # Subprocess helper
    ├── lint.py                 # Lint stage
    ├── test.py                 # Test stage
    └── build.py                # Build stage
```

## Workflows

### Lint (`lint.yml`)

Checks code formatting and style.

- **Runs:** `pipeline-runner lint`
- **Python:** 3.11
- **Fails on:** formatting drift or lint violations

### Test (`test.yml`)

Runs the full test suite across Python versions.

- **Runs:** `pipeline-runner test`
- **Matrix:** Python 3.11, 3.12, 3.13
- **Fails on:** any test failure

### Build (`build.yml`)

Builds and validates the distributable package.

- **Runs:** `pipeline-runner build`
- **Artifacts:** uploads `dist/` (sdist + wheel)
- **Fails on:** build error or broken entry point

## Design Decisions

**Why extract a pipeline library?**
Pipeline logic in YAML is hard to test, debug, and run locally. A Python package makes CI stages reproducible on any machine with `pip install ./tools/pipeline_runner && pipeline-runner all`.

**Why three separate workflows?**
Each concern (style, correctness, packaging) has a distinct failure mode and fix path. Splitting them gives faster feedback — a lint failure doesn't block seeing test results.

**Why `pipx install poetry`?**
GitHub-hosted runners include `pipx` by default. This avoids curl-based installer scripts and pinning installer versions.

**Why matrix only on tests?**
Lint rules and build output don't vary across Python versions. Testing does — different runtime behavior, stdlib changes, and dependency compatibility.

**Why verify the wheel?**
A package that builds but can't be installed or run is a silent failure. Installing the wheel and running `--help` catches missing entry points, broken imports, and packaging issues.

**Why no dependencies in pipeline-runner?**
The package only uses `subprocess` to call tools already installed by the main project's `poetry install`. This keeps the tool zero-dependency and avoids version conflicts.
