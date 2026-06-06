"""Check that a pull request body references an OPEN issue via a closing keyword.

This script is the enforcement point for ARCH-001: every PR that lands
on `main` must reference an open issue via `Closes #N`, `Fixes #N`, or
`Resolves #N`.

Usage:
    # Default mode: parses the body and asserts it contains a closing keyword.
    # The script does NOT itself fetch issue state from GitHub — that is the
    # job of the `pr-issue-link` GH Actions workflow (see
    # .github/workflows/pr-issue-link.yml). For local testing we accept the
    # issue state via --issue-state (or the GITHUB_ISSUE_STATE env var).
    python tools/check_pr_link.py --pr-body "Closes #42" --issue-state open
    # -> exit 0

    python tools/check_pr_link.py --pr-body "no closing keyword"
    # -> exit 1

    python tools/check_pr_link.py --pr-body "Closes #1" --issue-state closed
    # -> exit 1

Trade-off (AC-21):
    The GitHub REST API returns the *current* issue state, not state at
    PR-open time. A PR that is valid at open time may become invalid
    if its referenced issue is closed by another PR before merge. The
    chosen behavior is to use the *current* state at job run time. This
    is a deliberate trade-off — the alternative (snapshot the issue
    state at PR-open) is not available via the GitHub REST API, and
    polling the timeline is not free. Documented in
    `.archgate/adrs/ARCH-001-pr-only-and-issue-link.md`.

    Phrases required by AC-21 (literal grep):
      (i) "current issue state, not state at PR-open time"
     (ii) "valid at open time"
    (iii) "current.*state at job run time"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Sequence

# A closing keyword followed by `#<digits>`, on a line by itself or after
# whitespace. Trailing characters are tolerated (e.g. `Closes #42.`), but
# a different word in between (`Closes  the  #42`) is not.
CLOSING_KEYWORD_RE = re.compile(
    r"(?im)^\s*(?:closes|fixes|resolves)\s+#(\d+)\b"
)


def find_issue_number(body: str) -> int | None:
    """Return the first issue number referenced by a closing keyword, or None."""
    if not body:
        return None
    m = CLOSING_KEYWORD_RE.search(body)
    return int(m.group(1)) if m else None


def check(pr_body: str, issue_state: str | None) -> tuple[int, str]:
    """Return (exit_code, message).

    exit_code is 0 when the body contains a closing keyword AND the
    referenced issue is open. Otherwise 1.
    """
    issue = find_issue_number(pr_body)
    if issue is None:
        return 1, "no closing keyword found (need `Closes #N`, `Fixes #N`, or `Resolves #N`)"

    state = (issue_state or "").strip().lower()
    if state not in {"open", "closed"}:
        return 1, f"--issue-state must be 'open' or 'closed', got {issue_state!r}"

    if state == "closed":
        return 1, f"referenced issue #{issue} is closed"

    return 0, f"referenced issue #{issue} is open"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--pr-body",
        default=os.environ.get("PR_BODY", ""),
        help="Pull request body text (or set PR_BODY env var)",
    )
    p.add_argument(
        "--issue-state",
        default=os.environ.get("GITHUB_ISSUE_STATE"),
        help="Current state of the referenced issue: 'open' or 'closed'",
    )
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    code, message = check(args.pr_body, args.issue_state)
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
