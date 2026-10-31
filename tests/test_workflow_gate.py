"""The Test workflow's gate must not be able to pass vacuously.

The self-hosted leg is the preferred one and the canonical job falls back
to a GitHub-hosted runner when the leg does not pass, so the leg carries a
job level `continue-on-error`. That flag also reports the JOB as
successful when pytest failed, and the gate used to read the job result
only: a failing test suite was swallowed, the gate said "passed" and the
hosted legs skipped the work, so a green run proved nothing.

The gate therefore has to read the OUTCOME of the Test step, published as
a job output. The workflow is checked as text — the repository has no YAML
parser among its dependencies and one is not worth adding for this.

"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "test.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text()


def _gate_condition() -> str:
    match = re.search(
        r"- id: check\n(?:\s+#.*\n)*\s+if: (?P<condition>.+)\n",
        _workflow_text(),
    )
    assert match is not None, "the gate step has no if: condition"
    return match.group("condition")


def test_the_self_hosted_leg_publishes_the_outcome_of_the_test_step() -> None:
    # steps.test.outcome is "failure" when pytest failed and "skipped" when
    # an earlier step kept the suite from running: neither may pass.
    assert "steps.test.outcome == 'success'" in _workflow_text()


def test_the_gate_requires_the_published_test_outcome() -> None:
    assert "needs._test-self-hosted.outputs.passed == 'true'" in _gate_condition()


def test_the_gate_does_not_trust_the_job_result_alone() -> None:
    # The job result is only half of the truth: the job level
    # continue-on-error reports success even when pytest failed.
    condition = _gate_condition()
    assert "needs._test-self-hosted.result == 'success'" in condition
    assert "&&" in condition


def test_an_infrastructure_failure_still_does_not_fail_the_run() -> None:
    # The leg has to keep tolerating a broken runner — the canonical job
    # falls back to a GitHub-hosted runner for that.
    assert "continue-on-error: true" in _workflow_text()


def test_the_hosted_legs_run_unless_the_gate_passed() -> None:
    text = _workflow_text()
    assert text.count("needs._test-gate.outputs.passed != 'true'") >= 1
    assert "needs._test-gate.outputs.passed == 'true'" not in text
