# tests/test_policy_decisions.py
"""This repository obeys its own policy, and the verdict is data.

TWO CHECKS OF A DIFFERENT KIND, AND THE SPLIT IS THE POINT:

    policies/repo/repo_test.rego   are the rules well-formed and tested?
    this file                      does the CODEBASE satisfy them?

A policy suite can be immaculate and enforce nothing. Only evaluating real
input proves the repository obeys its own rules.

THE ASSERTIONS ARE ON THE VERDICT OBJECT, NOT ON AN EXIT CODE. A gate that
prints and exits leaves nothing to query later; a gate that emits a verdict with
rule ids and reason codes can be aggregated, trended and explained. These tests
assert that shape as strictly as they assert the outcome.

VERDICT IS IMPORTED FROM contracts, NOT FROM evaluate. mypy --strict refuses an
import of a name a module did not explicitly export, and it is right to: reading
the contract through the module that happens to use it makes the dependency
invisible and lets the real source move without a failing import.
"""

from pathlib import Path

import pytest

from cscie103_olap_oltp.contracts.verdict import Decision, Verdict, Violation
from cscie103_olap_oltp.policy.evaluate import evaluate_repository, opa_available

# WITHOUT OPA THERE IS NO VERDICT, AND "no verdict" IS NOT "pass". Skipping is
# the honest outcome: the check did not run. A try/except returning success
# here would be the fail-open shape this project exists to remove.
requires_opa = pytest.mark.skipif(
    not opa_available(),
    reason="opa is not on PATH; run inside `nix develop`",
)


@requires_opa
def test_repository_satisfies_its_own_policy() -> None:
    """The whole point: real files, real rules, real verdict."""
    verdict = evaluate_repository()
    assert verdict.outcome == "pass", verdict.explain()


@requires_opa
def test_verdict_is_three_valued() -> None:
    """pass / fail / unknown. Absence of evidence is never silently a pass."""
    verdict = evaluate_repository()
    assert verdict.outcome in {"pass", "fail", "unknown"}


@requires_opa
def test_verdict_names_the_policy_revision() -> None:
    """A verdict that cannot say WHICH rules produced it is not auditable.

    Six months later the question is never "did it pass" but "what did it check
    when it passed", and that answer has to travel with the verdict.
    """
    verdict = evaluate_repository()
    assert len(verdict.policy_revision) == 64


@requires_opa
def test_every_violation_carries_id_and_reason_code() -> None:
    """Violations as Data: machine-explainable, never just 'failed'.

    ATTRIBUTE ACCESS, NOT SUBSCRIPTING. A Violation is a model, not a dict, and
    that distinction is what gives the fields types instead of Any.
    """
    verdict = evaluate_repository()
    for violation in verdict.violations:
        assert violation.id
        assert violation.reason_code
        assert violation.message


@requires_opa
def test_verdict_serialises_to_json_losslessly() -> None:
    """The verdict is written to .artifacts/ and queried later, so it must
    round-trip through JSON without losing the fields a query needs."""
    verdict = evaluate_repository()
    restored = Verdict.model_validate_json(verdict.model_dump_json())
    assert restored == verdict


def test_missing_opa_yields_unknown_not_pass() -> None:
    """A tool that is absent produces `unknown`, and unknown is not success.

    This is the rule that keeps the gate honest on a machine without the
    toolchain: the check did not run, so it cannot report that it passed.
    """
    verdict = evaluate_repository(opa_path="/nonexistent/opa")
    assert verdict.outcome == "unknown"
    assert verdict.reason_code == "POLICY_ENGINE_UNAVAILABLE"


def test_explain_is_human_readable_and_machine_derived() -> None:
    """explain() renders the SAME data the verdict carries, not a second
    narrative that can drift from it."""
    verdict = evaluate_repository(opa_path="/nonexistent/opa")
    assert "POLICY_ENGINE_UNAVAILABLE" in verdict.explain()


def test_every_decision_is_appended_to_the_ledger(tmp_path: Path) -> None:
    """DECISIONS AS DATA REQUIRES AN ORDERED RECORD, NOT A PILE OF FILES.

    Per-run artifacts answer "what did this run decide". Only a chain answers
    "what has this platform decided, in what order, unaltered" -- which is the
    question an audit actually asks.
    """
    from cscie103_olap_oltp.ledger import verify_chain
    from cscie103_olap_oltp.policy.__main__ import record

    ledger = tmp_path / "ledger.jsonl"
    verdict = Verdict(
        contract="repository/v1",
        outcome="fail",
        reason_code="POLICY_VIOLATIONS",
        policy_revision="0" * 64,
        violations=(Violation(id="R001", reason_code="X", message="m"),),
        evidence=(),
    )
    decision = Decision(verdict=verdict, action="halt", reason_code="POLICY_VIOLATIONS")

    record(decision, artifacts=tmp_path / "verdicts", ledger=ledger)

    result = verify_chain(ledger)
    assert result.entries == 1
    assert result.intact


def test_the_ledger_record_carries_the_outcome_and_action(tmp_path: Path) -> None:
    """A RECORD THAT CANNOT BE QUERIED IS NOT DATA.

    The payload has to carry the fields someone would filter on -- which
    contract, what outcome, what the platform did about it.
    """
    import json

    from cscie103_olap_oltp.policy.__main__ import record

    ledger = tmp_path / "ledger.jsonl"
    verdict = Verdict(
        contract="repository/v1",
        outcome="pass",
        reason_code="POLICY_SATISFIED",
        policy_revision="0" * 64,
        violations=(),
        evidence=(),
    )
    decision = Decision(verdict=verdict, action="publish", reason_code="POLICY_SATISFIED")

    record(decision, artifacts=tmp_path / "verdicts", ledger=ledger)

    payload = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])["payload"]
    assert payload["contract"] == "repository/v1"
    assert payload["outcome"] == "pass"
    assert payload["action"] == "publish"


def test_the_artifact_is_still_written(tmp_path: Path) -> None:
    """THE CHAIN SUMMARISES; THE ARTIFACT CARRIES THE DETAIL.

    Putting whole verdicts in the ledger would make every line enormous and the
    chain unreadable, so the record points at the artifact rather than
    duplicating it.
    """
    from cscie103_olap_oltp.policy.__main__ import record

    artifacts = tmp_path / "verdicts"
    verdict = Verdict(
        contract="repository/v1",
        outcome="pass",
        reason_code="POLICY_SATISFIED",
        policy_revision="0" * 64,
        violations=(),
        evidence=(),
    )
    decision = Decision(verdict=verdict, action="publish", reason_code="POLICY_SATISFIED")

    written = record(decision, artifacts=artifacts, ledger=tmp_path / "ledger.jsonl")
    assert written.is_file()
    assert written.parent == artifacts
