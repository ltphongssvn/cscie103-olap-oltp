# src/cscie103_olap_oltp/policy/ruleset.py
"""Verify branch protection by its CONTENTS, not by its existence.

A ruleset that exists and requires nothing is the failure mode that looks like
success. The settings page shows protection, the API reports a rule, and every
pull request merges unchecked -- which is the state this repository's first pull
request was opened in.

WHY THIS IS PYTHON AND NOT REGO. The repository policy in policies/repo/ judges
files on disk; this judges a live server. Keeping them separate means an offline
gate stays offline, and a network check cannot silently become a precondition
for committing.

THE VIOLATIONS SHARE THE Violation CONTRACT with everything else in this
project, so a protection failure aggregates alongside a data-quality failure
rather than living in its own reporting dialect.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from cscie103_olap_oltp.contracts.verdict import Violation

# THE CI JOB NAME, WHICH IS ALSO THE REQUIRED CHECK CONTEXT. GitHub matches
# required checks by this exact string and does NOT warn when a renamed job
# stops reporting -- the requirement simply never becomes satisfiable. A test
# reads the workflow and compares, because two copies of one string always
# drift.
REQUIRED_CHECK = "quality gate"

RULESET_NAME = "develop-and-main-protection"

# BOTH BRANCHES. Protecting develop but not main leaves the release branch open,
# and main is where a tag is cut from.
PROTECTED_REFS = ("refs/heads/develop", "refs/heads/main")

# GITFLOW DEPENDS ON MERGE TOPOLOGY. Squash collapses the commits whose
# reasoning is the most valuable artifact in this history; rebase rewrites
# hashes that a verdict's provenance may already reference.
ALLOWED_MERGE_METHODS = ["merge"]


def _rules_by_type(document: dict[str, Any], rule_type: str) -> list[dict[str, Any]]:
    return [rule for rule in document.get("rules", []) if rule.get("type") == rule_type]


def ruleset_violations(document: dict[str, Any]) -> list[Violation]:
    """Every way this ruleset fails to protect the repository.

    A PURE FUNCTION OVER A DOCUMENT, so it can judge the committed contract and
    the server's actual state with identical logic. If the two were checked by
    different code, `repo:configure` could install something `check:ruleset`
    rejects and neither would be obviously wrong.

    RETURNS A LIST, NOT A BOOLEAN. "Protection is wrong" is not actionable;
    "the required status check is missing" is.
    """
    findings: list[Violation] = []

    # ENFORCEMENT FIRST. An `evaluate`-mode ruleset reports and permits, so
    # every other rule below could be perfect and nothing would be enforced.
    if document.get("enforcement") != "active":
        findings.append(
            Violation(
                id="P001",
                reason_code="RULESET_NOT_ACTIVE",
                message=f"enforcement is {document.get('enforcement')!r}, not 'active'",
            )
        )

    included = document.get("conditions", {}).get("ref_name", {}).get("include", [])
    for ref in PROTECTED_REFS:
        if ref not in included:
            findings.append(
                Violation(
                    id="P002",
                    reason_code="BRANCH_NOT_PROTECTED",
                    message=f"{ref} is not covered by the ruleset",
                )
            )

    pull_request_rules = _rules_by_type(document, "pull_request")
    if not pull_request_rules:
        findings.append(
            Violation(
                id="P003",
                reason_code="NO_PULL_REQUEST_REQUIRED",
                message="the ruleset does not require a pull request",
            )
        )

    for rule in pull_request_rules:
        methods = rule.get("parameters", {}).get("allowed_merge_methods", [])
        if methods != ALLOWED_MERGE_METHODS:
            findings.append(
                Violation(
                    id="P004",
                    reason_code="MERGE_METHOD_NOT_MERGE_COMMIT",
                    message=(f"allowed merge methods are {methods}, not {ALLOWED_MERGE_METHODS}"),
                )
            )

    # THE RULE THAT MAKES AUTO-MERGE SAFE. Without a required check, auto-merge
    # has nothing to wait for and fires immediately.
    contexts = {
        check.get("context")
        for rule in _rules_by_type(document, "required_status_checks")
        for check in rule.get("parameters", {}).get("required_status_checks", [])
    }
    if REQUIRED_CHECK not in contexts:
        findings.append(
            Violation(
                id="P005",
                reason_code="NO_REQUIRED_STATUS_CHECK",
                message=(
                    f"{REQUIRED_CHECK!r} is not a required status check; found {sorted(contexts)}"
                ),
            )
        )

    return findings


def fetch_ruleset(name: str = RULESET_NAME) -> dict[str, Any]:
    """Read the ruleset the server is actually enforcing.

    RAISES ON ABSENCE. Returning an empty document would flow into
    ruleset_violations() and produce a tidy list of findings that describe
    nothing -- the check would appear to have run against a real ruleset.
    """
    if shutil.which("gh") is None:
        raise RuntimeError("gh is not on PATH; run inside `nix develop`")

    # NO noqa HERE, AND THAT ASYMMETRY IS THE POINT. Ruff distinguishes a
    # literal argument list from a computed one: this call takes only string
    # literals, so S603 does not fire. The second call interpolates an id and
    # does fire. Suppressing both would hide the distinction the linter is
    # drawing.
    listing = subprocess.run(
        ["gh", "api", "repos/{owner}/{repo}/rulesets"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    matches = [item for item in json.loads(listing.stdout) if item.get("name") == name]
    if not matches:
        raise RuntimeError(f"no ruleset named {name!r} exists on the server")

    # THE LIST ENDPOINT OMITS `rules` AND `conditions`. Judging its output would
    # report every rule as missing -- a false failure that teaches people to
    # ignore the check. The detail endpoint is the one that carries them.
    #
    # S603 IS SUPPRESSED NARROWLY: the interpolated value is an integer id read
    # from the GitHub API's own response, never from user input, and the command
    # is a fixed argument list with no shell.
    ruleset_id = matches[0]["id"]
    detail = subprocess.run(  # noqa: S603
        ["gh", "api", f"repos/{{owner}}/{{repo}}/rulesets/{ruleset_id}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    result: dict[str, Any] = json.loads(detail.stdout)
    return result
