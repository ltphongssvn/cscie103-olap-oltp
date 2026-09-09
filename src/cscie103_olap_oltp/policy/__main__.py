# src/cscie103_olap_oltp/policy/__main__.py
"""The enforcement point: verdict in, exit code and artifact out.

    Verdict as Data -> Decision as Data -> Enforcement

THE VERDICT IS WRITTEN BEFORE THE PROCESS EXITS, AND THAT ORDER IS DELIBERATE.
A gate that exits non-zero and leaves nothing behind forces the next person to
re-run it to find out what happened -- and a re-run observes a different moment.
The artifact is the record; the exit code is only how the shell learns of it.

`unknown` EXITS NON-ZERO. A check that did not run has not passed, and a build
that treats "could not check" as success is precisely the fail-open shape this
repository removes everywhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from cscie103_olap_oltp.contracts.verdict import Action, Decision, Outcome
from cscie103_olap_oltp.policy.evaluate import evaluate_repository
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

# GITIGNORED BY DESIGN. Evidence belongs in a store, not in the source history:
# committing it would make every gate run a diff, and the repository would
# record its own observations as though they were intent.
ARTIFACTS = REPO_ROOT / ".artifacts" / "verdicts"


class Response(NamedTuple):
    """What the platform does about an outcome.

    A NAMED TUPLE RATHER THAN A BARE TUPLE, because mypy --strict widens
    `("halt", "X", 1)` to `tuple[str, str, int]` and the Literal is lost --
    which is exactly how a typo like "hault" reaches Decision() unnoticed. The
    annotation is the check.
    """

    action: Action
    reason_code: str
    exit_code: int


# THE RESPONSE FOR EACH OUTCOME, DECLARED AS DATA RATHER THAN BRANCHED IN CODE.
# Changing what a `fail` does is then an edit to a table, reviewable on its own.
#
# THE KEY TYPE IS Outcome, SO THE TABLE MUST BE TOTAL. Adding a fourth outcome
# to the contract fails here at type-check time rather than raising KeyError at
# the moment the gate was supposed to protect something.
RESPONSES: dict[Outcome, Response] = {
    "pass": Response("publish", "POLICY_SATISFIED", 0),
    "fail": Response("halt", "POLICY_VIOLATIONS", 1),
    "unknown": Response("halt", "POLICY_NOT_EVALUATED", 1),
}


def main() -> None:
    verdict = evaluate_repository()
    response = RESPONSES[verdict.outcome]
    decision = Decision(
        verdict=verdict,
        action=response.action,
        reason_code=response.reason_code,
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = decision.decided_at.strftime("%Y%m%dT%H%M%S%fZ")
    path: Path = ARTIFACTS / f"{stamp}.json"
    path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")

    print(verdict.explain())
    print(f"decision: {response.action} -> {path.relative_to(REPO_ROOT)}")
    raise SystemExit(response.exit_code)


if __name__ == "__main__":
    main()
