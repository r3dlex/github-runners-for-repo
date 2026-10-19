# RULES

Local safety rules for this repo. Keep this file short.

## 1. Operational state stays out of git
- Do not commit `.omc/**` except `.omc/skills/**` (the intentional committable exception per global CLAUDE.md).
- Do not commit `.omx/**`.
- These belong in `.gitignore` before any PR touches them.

## 2. No host policy mutation without explicit opt-in
- Treat GitHub / ADO / GitLab / Jira branch and policy settings as checklist output by default.
- Do not edit branch protection, change required checks, mutate CODEOWNERS, or call admin endpoints without explicit user opt-in for that specific change.
- Single-owner merge deadlock uses the documented temp-relax `enforce_admins` -> admin `--squash` -> restore sequence — that workaround is opt-in only.

## 3. Do not bypass autobahn fail-closed gates
- Autobahn gates are fail-closed. Do not skip, override, or hand-edit gate outputs.
- Do not commit `--no-verify` or `.gitignore` shortcuts that hide failing checks.
- Do not add `continue-on-error` to silence block-level failures — except on the self-hosted leg of a workflow as the documented self-hosted-to-GH-hosted fallback (the same workflow's GH-hosted leg still gates the merge).

## 4. Toolchain
- `uv` only (ADR-003). Do not introduce Poetry, Pipenv, or `pipeline-runner`.

## 5. Test discipline
- Every change passes `uv run pytest` before commit.
- Coverage floor 95% (ADR-004). Do not lower the threshold to land a change; raise the coverage instead.
