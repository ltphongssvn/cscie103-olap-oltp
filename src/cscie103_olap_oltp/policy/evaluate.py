# src/cscie103_olap_oltp/policy/evaluate.py
"""Evaluate this repository against its own policy and emit a verdict.

THE JOIN BETWEEN THE TWO HALVES. snapshot.py answers what IS true; repo.rego
states what MUST be true; this module runs one against the other and records the
result as data rather than as an exit code.

WHY A VERDICT AND NOT A BOOLEAN. An exit code cannot be aggregated, trended, or
explained six months later. `.artifacts/verdicts/` accumulates a queryable
history of every gate run, which is the same OLAP argument this project is
built to demonstrate -- applied to the platform's own evidence.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cscie103_olap_oltp.contracts.verdict import Verdict, Violation
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT, build_snapshot

CONTRACT = "repository/v1"
POLICY_PACKAGE = "data.repo"


def opa_available(opa_path: str = "opa") -> bool:
    """Whether the policy engine can be invoked at all.

    Separate from evaluation so a test can SKIP honestly rather than passing
    without having run. "Could not check" and "checked and passed" are different
    facts, and only one of them is evidence.
    """
    return shutil.which(opa_path) is not None


def policy_revision(policy_dir: Path) -> str:
    """SHA-256 over every policy source, in sorted order.

    DERIVED, NOT DECLARED. A hand-maintained version string describes what
    someone remembered to update; a digest describes the files that actually
    produced the verdict and cannot drift from them.

    Sorted so the digest is stable across filesystems: directory iteration order
    is not guaranteed, and an unstable digest would make every verdict look like
    it came from a different policy.
    """
    digest = hashlib.sha256()
    for path in sorted(policy_dir.rglob("*.rego")):
        digest.update(path.relative_to(policy_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _unknown(reason_code: str, revision: str) -> Verdict:
    """A verdict for a check that did not run.

    NOT A FAILURE AND NOT A PASS. Reporting `fail` would train people to ignore
    the gate on machines without the toolchain; reporting `pass` would
    manufacture confidence. `unknown` is the only honest third answer.
    """
    return Verdict(
        contract=CONTRACT,
        outcome="unknown",
        reason_code=reason_code,
        policy_revision=revision,
    )


def evaluate_repository(
    root: Path = REPO_ROOT,
    opa_path: str = "opa",
) -> Verdict:
    """Run the repository snapshot against the repository policy."""
    policy_dir = root / "policies"
    revision = policy_revision(policy_dir)

    if not opa_available(opa_path):
        return _unknown("POLICY_ENGINE_UNAVAILABLE", revision)

    snapshot: dict[str, Any] = build_snapshot(root)

    # --stdin-input KEEPS THE SNAPSHOT OFF DISK AND OUT OF ARGV. A temp file
    # would leave the input behind on a crash; an argv payload is visible to
    # every process on the machine.
    completed = subprocess.run(  # noqa: S603
        [
            opa_path,
            "eval",
            "--format",
            "json",
            "--data",
            str(policy_dir),
            "--stdin-input",
            f"{POLICY_PACKAGE}.deny",
        ],
        input=json.dumps(snapshot),
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.returncode != 0:
        return _unknown("POLICY_ENGINE_ERROR", revision)

    # OPA RETURNS AN EMPTY `result` LIST WHEN THE QUERY IS UNDEFINED, WHICH IS
    # NOT THE SAME AS AN EMPTY DENY SET. Treating the two alike would report a
    # pass for a mistyped package path -- the vacuous-gate failure R005 exists
    # to prevent, reproduced in the code that enforces it.
    payload = json.loads(completed.stdout)
    results = payload.get("result", [])
    if not results:
        return _unknown("POLICY_QUERY_UNDEFINED", revision)

    raw = results[0]["expressions"][0]["value"]
    violations = tuple(
        Violation(id=item["id"], reason_code=item["reason_code"], message=item["message"])
        for item in sorted(raw, key=lambda item: item["id"])
    )

    return Verdict(
        contract=CONTRACT,
        outcome="fail" if violations else "pass",
        reason_code="POLICY_VIOLATIONS" if violations else "POLICY_SATISFIED",
        policy_revision=revision,
        violations=violations,
    )
