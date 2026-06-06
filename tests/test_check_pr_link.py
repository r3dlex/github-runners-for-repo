"""Unit tests for tools/check_pr_link.py — AC-5 and AC-21 behavior."""

from __future__ import annotations

import pytest

from tools.check_pr_link import check, find_issue_number


class TestFindIssueNumber:
    def test_closes_keyword(self):
        assert find_issue_number("Closes #42") == 42

    def test_fixes_keyword(self):
        assert find_issue_number("Fixes #7") == 7

    def test_resolves_keyword(self):
        assert find_issue_number("Resolves #99") == 99

    def test_no_keyword(self):
        assert find_issue_number("Just a body, no closing keyword.") is None

    def test_keyword_with_trailing_punct(self):
        # The regex requires the digit run to end; trailing punctuation is fine.
        assert find_issue_number("Closes #42.") == 42

    def test_empty_body(self):
        assert find_issue_number("") is None

    def test_case_insensitive(self):
        assert find_issue_number("closes #5") == 5

    def test_multiline_body(self):
        body = "## Summary\n\nFix the bug.\n\nCloses #123"
        assert find_issue_number(body) == 123

    def test_ignores_issue_link_without_keyword(self):
        # `See #42` is not a closing keyword.
        assert find_issue_number("See #42") is None


class TestCheck:
    def test_no_closing_keyword_fails(self):
        code, _ = check("no closing keyword here", "open")
        assert code == 1

    def test_open_issue_passes(self):
        code, msg = check("Closes #1", "open")
        assert code == 0
        assert "open" in msg

    def test_closed_issue_fails(self):
        code, _ = check("Closes #1", "closed")
        assert code == 1

    def test_missing_state_fails(self):
        code, _ = check("Closes #1", None)
        assert code == 1

    def test_invalid_state_fails(self):
        code, _ = check("Closes #1", "weird")
        assert code == 1


def test_docstring_contains_ac21_phrases():
    """AC-21: the script's docstring MUST contain all three required phrases."""
    import tools.check_pr_link as mod

    doc = mod.__doc__ or ""
    assert "current issue state, not state at PR-open time" in doc
    assert "valid at open time" in doc
    assert re.search(r"current.*state at job run time", doc) is not None


import re
