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
leaves no reviewable record of intent. The pure function is unit-tested against
the file; the marked test compares the server to it.

THE FIXTURE IS TYPED dict[str, Any], NOT dict[str, object]. `object` makes every
nested access an error, which the first version of this file buried under
guessed `type: ignore` codes -- and mypy's ignore-without-code and
warn_unused_ignores rejected them, correctly. A suppression that has to be
guessed is a signal the type is wrong, not that the checker is.
"""

import json
from typing import Any

import pytest

from cscie103_olap_oltp.policy.ruleset import (
    REQUIRED_CHECK,
    fetch_ruleset,
    ruleset_violations,
)
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACT_PATH = REPO_ROOT / "contracts" / "ruleset-develop-and-main.json"


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

    Otherwise `repo:configure` would install a ruleset that `check:ruleset`
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


@pytest.mark.integration
def test_server_ruleset_matches_the_contract() -> None:
    """The only test that proves the SERVER is protected.

    Marked integration because it needs network and gh auth; a local commit
    cannot weaken a server-side ruleset, so gating every commit on this would
    block offline work for no benefit.
    """
    assert ruleset_violations(fetch_ruleset()) == []
