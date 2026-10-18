# Contributing

This repo runs on autobahn. See `AGENTS.md` for the full agent + human behavior contract, the bootstrap protocol, and the 8-check pipeline.

## Quick rules
- Every PR must reference an open issue with `Closes #N` / `Fixes #N` (enforced by `pr-issue-link` + archgate ADR-001).
- All 8 required CI checks must pass. No merges with red.
- Coverage stays at >=95% (ADR-004).
- Use `uv` (ADR-003) — no Poetry, Pipenv, or `pipeline-runner`.
- 1 approving review; admin is NOT exempt (ADR-002). Single-owner self-approval blocks use the documented temp-relax `enforce_admins` -> admin `--squash` -> restore sequence.

## Workflow
1. Bootstrap once: `uv sync && uvx pre-commit install --hook-type pre-commit --hook-type pre-push`.
2. Make the smallest viable change that achieves the goal.
3. Run `uvx pre-commit run --all-files` and `uvx pre-commit run --hook-stage pre-push --all-files` before pushing.
4. Open a PR, link the issue, ship it.
