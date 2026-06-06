## Summary

<!-- A 1-3 sentence description of what this PR changes. -->

## Linked issue

<!-- The `pr-issue-link` job requires one of the closing keywords
     below on a line by itself. The issue must be OPEN at job run time
     (see tools/check_pr_link.py for the trade-off discussion). -->

Closes #

## Test plan

<!-- Checklist of how the change was tested. -->

- [ ] Tests added or updated
- [ ] `uv run pytest --cov=github_runners_for_repo` passes locally
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] archgate check passes (`.archgate/adrs/`)

## Checklist

- [ ] CODEOWNERS reviewed the change
- [ ] `docs/branch-protection.md` is still accurate if protection changed
