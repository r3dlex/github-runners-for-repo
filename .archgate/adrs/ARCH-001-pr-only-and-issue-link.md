---
id: ARCH-001
title: PR-only with mandatory issue link
domain: governance
rules: true
files: [".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/"]
---

# ARCH-001 — PR-only with mandatory issue link

## Context

The repo previously allowed direct pushes to `main`. Trace was missing
because there was no record of *why* a change was made — only a commit
message. To make every change traceable to a stated intent, every merge
to `main` must come from a pull request that references an open issue
via `Closes #N`, `Fixes #N`, or `Resolves #N`.

## Decision

1. **PR-only.** Direct pushes to `main` are blocked by branch
   protection. (See ARCH-002.)
2. **Linked issue required.** Every PR body must contain a closing
   keyword (`Closes #N`, `Fixes #N`, `Resolves #N`) where `#N` refers
   to an **open** issue at the time the `pr-issue-link` job runs.
3. **PR template pre-populates the field.** Opening a new PR shows the
   "Linked issue" line from `.github/PULL_REQUEST_TEMPLATE.md`.

The companion rule (`pr-template-and-issue-templates-exist`) asserts the
two template files exist; the live check is performed by the
`pr-issue-link` GH Actions workflow.

## Do's and Don'ts

**Do:**

- Reference the issue with one of the three closing keywords
  (`Closes`, `Fixes`, `Resolves`).
- Pick the keyword that matches the issue's intent (e.g., `Fixes` for
  a bug report).
- Re-run the `pr-issue-link` job after amending the body.

**Don't:**

- Don't reference a closed issue — the `pr-issue-link` job uses the
  *current* state of the referenced issue at job run time. (See
  `tools/check_pr_link.py` for the trade-off discussion.)
- Don't use a non-keyword link like `Issue: #N` — the regex requires
  `Closes` / `Fixes` / `Resolves`.

## Implementation Pattern

Good example — PR body:

```
## Summary

Fix a bug in the runner registration flow.

Closes #42
```

Bad example — would fail the `pr-issue-link` job:

```
## Summary

Fix a bug in the runner registration flow.

See issue #42 for context.
```

## Consequences

**Positive:** every change is traceable to a stated intent. The
project can answer "why was this changed?" by following the link.

**Negative:** the author must have an open issue before opening a PR.
For trivial changes (typo fixes, config tweaks) a maintenance issue
must be opened first. (See ARCH-002 amendment: a one-line docs change
referencing a maintenance issue is the canary pattern in AC-18.)

**Risks:** the closing-keyword regex is brittle. A future change to
GitHub's linkifier (e.g., `Close #42` without the trailing `s`) would
silently break the check. Mitigated by a small unit test in
`tests/test_check_pr_link.py`.

## Compliance and Enforcement

**Automated:**

- `.archgate/adrs/ARCH-001-pr-only-and-issue-link.rules.ts` —
  asserts `.github/PULL_REQUEST_TEMPLATE.md` and
  `.github/ISSUE_TEMPLATE/` exist.
- `.github/workflows/pr-issue-link.yml` — runs
  `python tools/check_pr_link.py` on every PR push.

**Manual:**

- The PR author confirms the referenced issue is the right one and
  is open at the time of merge.

## References

- ARCH-002 (main branch protection — the gate that enforces PR-only)
- `.github/PULL_REQUEST_TEMPLATE.md` (the pre-populated body)
- `tools/check_pr_link.py` (the script that performs the check)
- AC-21 (the "current state at job run time" doc requirement)
