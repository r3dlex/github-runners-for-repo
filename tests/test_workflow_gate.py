"""The Test workflow's gate must not be able to pass vacuously.

The self-hosted leg is the preferred one and the canonical job falls back
to a GitHub-hosted runner when the leg does not pass, so the leg carries a
job level `continue-on-error`. That flag also reports the JOB as
successful when pytest failed, and the gate used to read the job result
only: a failing test suite was swallowed, the gate said "passed" and the
hosted legs skipped the work, so a green run proved nothing.

The gate therefore has to read the OUTCOME of the Test step, published as
a job output. Each Python version writes its own output: a matrix job
that writes one shared name keeps only the last leg to finish, so a later
pass would erase an earlier failure. The workflow is checked as text —
the repository has no YAML parser among its dependencies and one is not
worth adding for this.

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
    condition = _gate_condition()
    for version in ("3_11", "3_12", "3_13"):
        assert f"needs._test-self-hosted.outputs.passed_{version} == 'true'" in condition


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


def _self_hosted_block(text: str) -> str:
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("  _test-self-hosted:"))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"  [A-Za-z0-9_-]+:", lines[i]):
            end = i
            break
    return "\n".join(lines[start:end])


def _published_output_names(block: str) -> list[str]:
    names: list[str] = []
    in_outputs = False
    for line in block.splitlines():
        if line.strip() == "outputs:":
            in_outputs = True
            continue
        if not in_outputs:
            continue
        if line.startswith("      ") and ":" in line:
            names.append(line.split(":", 1)[0].strip())
            continue
        break
    return names


def _matrix_outputs(
    text: str, outcomes: dict[str, str], finish_order: tuple[str, ...]
) -> dict[str, str]:
    """The outputs GitHub exposes from a matrix job.

    A shared output name is overridden by the last matrix job that runs.
    A name that only one leg writes is kept. That is the rule in
    `jobs.<job_id>.outputs`: "Ensure that the output name is unique,
    otherwise the last matrix job that runs will override the output value."
    """
    block = _self_hosted_block(text)
    published = _published_output_names(block)
    combined: dict[str, str] = {}
    for version in finish_order:
        value = "true" if outcomes[version] == "success" else "false"
        if 'echo "passed=${{' in block:
            written = {"passed": value}
        elif "passed_${VERSION//./_}" in block:
            written = {f"passed_{version.replace('.', '_')}": value}
        else:
            raise AssertionError("the self-hosted leg does not say which output it writes")
        for name, written_value in written.items():
            if name in published:
                combined[name] = written_value
    return combined


def _gate_would_pass(text: str, outputs: dict[str, str]) -> bool:
    """Whether the gate step's if-condition is true.

    Job-level continue-on-error makes a dependant see result == success
    even when pytest failed, so the result clause cannot save the gate.
    """
    ok = True
    for clause in (part.strip() for part in _gate_condition_from(text).split("&&")):
        if clause == "needs._test-self-hosted.result == 'success'":
            ok = ok and True
            continue
        prefix = "needs._test-self-hosted.outputs."
        suffix = " == 'true'"
        if clause.startswith(prefix) and clause.endswith(suffix):
            name = clause[len(prefix) : -len(suffix)]
            ok = ok and outputs.get(name) == "true"
            continue
        raise AssertionError(f"unrecognised gate clause: {clause}")
    return ok


def _gate_condition_from(text: str) -> str:
    match = re.search(
        r"- id: check\n(?:\s+#.*\n)*\s+if: (?P<condition>.+)\n",
        text,
    )
    assert match is not None, "the gate step has no if: condition"
    return match.group("condition")


def test_a_later_passing_leg_does_not_hide_an_earlier_failure() -> None:
    """3.11 fails, then 3.12 and 3.13 pass. The hosted job must still run pytest.

    The last leg to finish is 3.13. If every leg writes the same output
    name, that pass replaces the failure and the hosted suite is skipped.
    """
    text = _workflow_text()
    outputs = _matrix_outputs(
        text,
        {"3.11": "failure", "3.12": "success", "3.13": "success"},
        ("3.11", "3.12", "3.13"),
    )
    assert not _gate_would_pass(text, outputs), (
        f"a failing 3.11 must not be erased by a later passing 3.13: the gate saw {outputs}"
    )
