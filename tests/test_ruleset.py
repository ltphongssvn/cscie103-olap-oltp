# tests/test_ruleset.py
"""Branch protection is verified by CONTENTS, not by existence.

A ruleset that exists and requires nothing is the failure mode that looks like
success: the settings page shows protection, the API reports a rule, and every
pull request merges unchecked. This repository shipped its first pull request in
exactly that state, which is why the check exists.

TWO LAYERS, DELIBERATELY SPLIT:

    the committed contract    contracts/ruleset-develop-and-main.json
    the server's actual state fetched from the API

Testing only the file proves nothing about the server. Testing only the server
leaves no reviewable record of intent.

WHY A MISSING TOKEN IS NOT UNIFORMLY A SKIP, WHICH IS THE INTERESTING PART.

When this test first ran in CI it died with `CalledProcessError ... exit status
4` -- a traceback that reads "protection is broken" and actually meant "gh has
no credentials". The obvious repair is `pytest.skip` on missing auth. That
repair is wrong, and would have been worse than the bug: a skipped test makes
the job green while verifying nothing, which is the same fail-open shape as
wrapping a test in `|| true`. The ecosystem agrees loudly enough that a plugin
exists whose entire purpose is turning skips into failures so CI cannot skip
tests because of missing dependencies.

So the question is not "do I have credentials" but "was this environment
supposed to have them". A laptop was never promised a token, and skipping there
is honest. A runner promises one; its absence is a defect in the workflow, and
the only useful outcome is a failure that says so.

That is the same three-valued logic the verdict contract already uses: `unknown`
is tolerable where nothing was promised and is a failure where something was.
"""

import json
from typing import Any

import pytest

from cscie103_olap_oltp.environment import current
from cscie103_olap_oltp.git.ghcli import NotAuthenticatedError
from cscie103_olap_oltp.policy.ruleset import (
    REQUIRED_CHECK,
    fetch_ruleset,
    ruleset_violations,
)
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACT_PATH = REPO_ROOT / "contracts" / "ruleset-develop-and-main.json"


def credentials_are_promised() -> bool:
    """Whether this environment undertook to supply gh credentials.

    CI IS SET BY EVERY MAJOR RUNNER, GitHub Actions included, and is the
    conventional signal for "this is automation, not a workstation". Reading it
    is what lets one test be honest on a laptop and strict on a runner without
    two copies of the test.
    """
    return current().ci


def _contract() -> dict[str, Any]:
    """A fresh copy per test.

    Returned by value rather than cached at module scope: every negative test
    mutates one field, and a shared dict would let one test's mutation decide
    another test's outcome.
    """
    parsed: dict[str, Any] = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    return parsed


def _reason_codes(document: dict[str, Any]) -> set[str]:
    return {violation.reason_code for violation in ruleset_violations(document)}


def test_committed_contract_satisfies_its_own_checks() -> None:
    """The file we PUT to GitHub must itself pass the verification.

    Otherwise `repo:configure` would install a ruleset that the verification
    immediately rejects -- two halves of the same intent disagreeing.
    """
    assert ruleset_violations(_contract()) == []


def test_missing_required_status_check_is_a_violation() -> None:
    """Without a REQUIRED check, auto-merge has nothing to wait for.

    It fires immediately, which is worse than no auto-merge because it looks
    like a gate.
    """
    document = _contract()
    document["rules"] = [
        rule for rule in document["rules"] if rule["type"] != "required_status_checks"
    ]
    assert "NO_REQUIRED_STATUS_CHECK" in _reason_codes(document)


def test_wrong_check_name_is_a_violation() -> None:
    """The context string must match the CI job name exactly.

    A renamed job silently stops being required: GitHub does not warn that a
    required check no longer reports.
    """
    document = _contract()
    for rule in document["rules"]:
        if rule["type"] == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [{"context": "renamed"}]
    assert "NO_REQUIRED_STATUS_CHECK" in _reason_codes(document)


def test_squash_merge_is_a_violation() -> None:
    """GitFlow depends on merge topology.

    Squashing collapses the commits whose reasoning is the most valuable thing
    in this history into a single message nobody wrote.
    """
    document = _contract()
    for rule in document["rules"]:
        if rule["type"] == "pull_request":
            rule["parameters"]["allowed_merge_methods"] = ["squash"]
    assert "MERGE_METHOD_NOT_MERGE_COMMIT" in _reason_codes(document)


def test_disabled_enforcement_is_a_violation() -> None:
    """An `evaluate`-mode ruleset reports and permits. That is not protection."""
    document = _contract()
    document["enforcement"] = "evaluate"
    assert "RULESET_NOT_ACTIVE" in _reason_codes(document)


def test_both_branches_must_be_covered() -> None:
    """Protecting develop but not main leaves the release branch open."""
    document = _contract()
    document["conditions"]["ref_name"]["include"] = ["refs/heads/develop"]
    assert "BRANCH_NOT_PROTECTED" in _reason_codes(document)


def test_missing_pull_request_rule_is_a_violation() -> None:
    """Direct pushes to develop defeat every other rule here."""
    document = _contract()
    document["rules"] = [rule for rule in document["rules"] if rule["type"] != "pull_request"]
    assert "NO_PULL_REQUEST_REQUIRED" in _reason_codes(document)


def test_required_check_name_matches_the_ci_job() -> None:
    """The constant and the workflow are two copies of one string.

    Reading the workflow here is what keeps them from drifting silently.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f"name: {REQUIRED_CHECK}" in workflow


def test_the_workflow_passes_a_token_to_the_gate() -> None:
    """The gate shells out to gh, so the step must carry GH_TOKEN.

    THIS TEST EXISTS BECAUSE ITS ABSENCE COST A CI RUN. Folding the ruleset
    check into `mise run check` left it in a step with no token, and gh exited 4
    -- "not authenticated" -- which reads like a missing ruleset. Offline and
    cheap, so the mistake cannot recur silently.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "GH_TOKEN:" in workflow


@pytest.mark.integration
def test_server_ruleset_matches_the_contract() -> None:
    """The only test that proves the SERVER is protected.

    ON A RUNNER, MISSING CREDENTIALS ARE A FAILURE. The environment promised a
    token; its absence means the workflow is misconfigured, and skipping would
    leave a permanently green job that verifies nothing.

    ON A WORKSTATION they are a skip, because nothing promised them -- and the
    skip is loud rather than silent: pytest's -ra reports the reason on every
    run, so a laptop that has quietly stopped being able to check this says so.
    """
    try:
        document = fetch_ruleset()
    except NotAuthenticatedError as error:
        if credentials_are_promised():
            pytest.fail(f"CI must supply gh credentials to this gate.\n{error}")
        pytest.skip(str(error))

    assert ruleset_violations(document) == []
