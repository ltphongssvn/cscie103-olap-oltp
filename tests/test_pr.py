# tests/test_pr.py
"""Merging is authorised by observed checks, never by intent.

THE MERGE ENDPOINT VERIFIES NOTHING. GitHub's own documentation is explicit that
it does not care about failing checks, and a 200 response can carry
`merged: false`. So the shape has to be:

    OBSERVE   wait for checks to register, watch to completion, let the rollup
              settle
    VERIFY    every conclusion acceptable, none pending, and not zero
    ACT       merge synchronously
    OBSERVE   parse the response and confirm merged is true

WHY NOT AUTO-MERGE. Since March 2026 it cannot be ENABLED until every
requirement is already met, returning 422 -- useful only where it is
unnecessary. The sibling project hit exactly that: "Required status check
'quality gate' is queued", after which the pull request sat green and unmerged.

THE UNIT TESTS HERE COVER THE VERIFY STEP AND THE BOUNDARY MODELS, because those
are where a fail-open bug hides. The network dance is exercised by using the
task, not by mocking gh into agreeing with me.
"""

import pytest

from cscie103_olap_oltp.git.pr import (
    PASSING_CONCLUSIONS,
    Check,
    CheckConclusion,
    MergeResult,
    PullRequest,
    verify_checks,
)


def _pr(**overrides: object) -> PullRequest:
    payload: dict[str, object] = {
        "number": 1,
        "state": "OPEN",
        "mergeStateStatus": "CLEAN",
        "url": "https://example.invalid/pull/1",
        "statusCheckRollup": [{"name": "quality gate", "conclusion": "SUCCESS"}],
    }
    payload.update(overrides)
    return PullRequest.model_validate(payload)


def test_empty_conclusion_means_pending_not_unknown() -> None:
    """gh reports a pending check as "", not null.

    Without normalising it, the enum rejects the value and the whole poll dies
    on the most ordinary state a check can be in.
    """
    check = Check.model_validate({"name": "quality gate", "conclusion": ""})
    assert check.conclusion is None


def test_unrecognised_conclusion_is_rejected() -> None:
    """A value outside the documented set is a contract change, not a shrug.

    Silently accepting it is how automation reads a renamed field, keeps going,
    and surfaces days later as a merge nobody authorised.
    """
    with pytest.raises(ValueError, match="conclusion"):
        Check.model_validate({"name": "x", "conclusion": "MOSTLY_FINE"})


def test_neutral_and_skipped_are_passing() -> None:
    """A skipped check has not failed.

    Treating SKIPPED as failure would block every merge where a path filter
    excluded a job -- a gate that refuses correct work is abandoned.
    """
    assert CheckConclusion.NEUTRAL in PASSING_CONCLUSIONS
    assert CheckConclusion.SKIPPED in PASSING_CONCLUSIONS


def test_failure_is_not_passing() -> None:
    assert CheckConclusion.FAILURE not in PASSING_CONCLUSIONS
    assert CheckConclusion.CANCELLED not in PASSING_CONCLUSIONS
    assert CheckConclusion.TIMED_OUT not in PASSING_CONCLUSIONS


def test_no_checks_at_all_is_refused() -> None:
    """FAIL CLOSED. An empty rollup means the workflow never ran or the query
    returned nothing, and treating "nothing to object to" as approval is how a
    gate becomes decoration."""
    with pytest.raises(SystemExit, match="no checks"):
        verify_checks(())


def test_pending_check_is_refused() -> None:
    """Merging while a check is still running is merging unverified code."""
    checks = (Check(name="quality gate", conclusion=None),)
    with pytest.raises(SystemExit, match="still running"):
        verify_checks(checks)


def test_failing_check_is_refused_and_named() -> None:
    """The refusal names the check, because "merge refused" is not actionable."""
    checks = (
        Check(name="quality gate", conclusion=CheckConclusion.FAILURE),
        Check(name="lint", conclusion=CheckConclusion.SUCCESS),
    )
    with pytest.raises(SystemExit, match="quality gate"):
        verify_checks(checks)


def test_all_passing_is_allowed() -> None:
    checks = (Check(name="quality gate", conclusion=CheckConclusion.SUCCESS),)
    verify_checks(checks)


def test_checks_settled_requires_at_least_one_check() -> None:
    """An empty rollup is not "settled".

    `all()` over an empty sequence is True, which would report a pull request
    with no checks as fully concluded -- the vacuous-pass shape this project
    keeps removing.
    """
    assert _pr(statusCheckRollup=[]).checks_settled is False


def test_checks_settled_is_false_while_one_is_pending() -> None:
    pr = _pr(
        statusCheckRollup=[
            {"name": "quality gate", "conclusion": "SUCCESS"},
            {"name": "other", "conclusion": ""},
        ]
    )
    assert pr.checks_settled is False


def test_terminal_states_are_recognised() -> None:
    """A closed pull request is not waiting for anything."""
    assert _pr(state="MERGED").is_terminal
    assert _pr(state="CLOSED").is_terminal
    assert not _pr(state="OPEN").is_terminal


def test_merge_result_forbids_extra_fields() -> None:
    """The merge response's three fields ARE the contract.

    REST API version 2026-03-10 removed merge_commit_sha from pull request
    responses; automation that reads a missing field as null keeps going and
    deploys the wrong commit. Forbidding extras makes a contract change loud.
    """
    with pytest.raises(ValueError, match="extra"):
        MergeResult.model_validate({"merged": True, "sha": "abc", "message": "ok", "surprise": 1})


def test_merge_result_rejects_a_missing_field() -> None:
    with pytest.raises(ValueError, match="sha"):
        MergeResult.model_validate({"merged": True, "message": "ok"})
