---
id: ARCH-003
title: uv is the only Python toolchain
domain: toolchain
rules: true
files: ["pyproject.toml", "uv.lock", "tools/", ".github/workflows/*.yml"]
---

# ARCH-003 — uv is the only Python toolchain

## Context

The project used Poetry for dependency management and a custom
`tools/pipeline_runner/` to mirror the GH Actions stages locally. Both
add maintenance burden and create drift between local CI and the
GitHub Actions workflows.

`uv` (>=0.5) replaces both. It is the lockfile manager, the venv
provider, and the runner of project scripts. `uv.lock` is the single
lockfile. `tools/pipeline_runner/` is deleted.

## Decision

1. **No Poetry.** `[tool.poetry.*]` blocks in `pyproject.toml` are
   removed; the project uses PEP 621 `[project]` instead.
2. **No `poetry.lock`.** It is deleted from the repo and from CI.
3. **No `tools/pipeline_runner/`.** It is deleted. The 4 baseline
   workflows (`lint`, `test`, `coverage`, `build`) are rewritten to
   use `astral-sh/setup-uv@v4` + `uv run`.
4. **`uv.lock` is tracked.** Same reproducibility rule as `poetry.lock`
   was.

The companion rule (`no-poetry-references`) greps tracked files for
the literal string `poetry` and reports a violation if any matches
outside this ADR's own `Context` section.

## Do's and Don'ts

**Do:**

- Use `uv sync` for local setup and `uv run <cmd>` for project
  scripts.
- Use `uv tool install <tool>` for one-off CLIs (archgate, pre-commit,
  gitleaks).
- Commit `uv.lock` after a `uv lock` resolution.

**Don't:**

- Don't add `poetry` or `pipenv` to the dev environment.
- Don't check in a `requirements.txt` — `uv.lock` is the source of
  truth.

## Implementation Pattern

Good example — `pyproject.toml` PEP 621 form:

```toml
[project]
name = "github-runners-for-repo"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "requests>=2.32",
  "python-dotenv>=1.1",
  "docker>=7.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.4", "pytest-cov>=6.0", "ruff>=0.12"]
```

Bad example — Poetry block reintroduced:

```toml
[tool.poetry]
name = "github-runners-for-repo"
```

## Consequences

**Positive:** the project has one toolchain, one lockfile, and no
custom CI mirror. `uv run` is the only command an agent needs to
know.

**Negative:** contributors with Poetry in their muscle memory have to
adapt. Mitigated by `AGENTS.md`'s `## Development Workflow` section
which uses `uv` commands only.

**Risks:** the migration can break a developer's local environment
if `uv` is not on PATH. Mitigated by the install instructions in
`AGENTS.md` and the bootstrap step in the `archgate` GH Actions job.

## Compliance and Enforcement

**Automated:**

- `.archgate/adrs/ARCH-003-uv-only-toolchain.rules.ts` — greps for
  `poetry` in tracked files.
- `tools/check_no_poetry.sh` (a future addition) — fails the build if
  a Poetry block reappears in `pyproject.toml`.

**Manual:**

- A reviewer rejects a PR that adds `poetry` or `pipenv` references.

## References

- `.omc/plans/ralplan-python-modernize-shiftleft.md` §3 (Phase B)
- AC-7 (the "no Poetry anywhere" check)
- AGENTS.md (the `uv`-only Development Workflow)
