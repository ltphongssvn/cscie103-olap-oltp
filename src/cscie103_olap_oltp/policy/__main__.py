# src/cscie103_olap_oltp/policy/__main__.py
"""The enforcement point: verdict in, exit code and durable record out.

    Verdict as Data -> Decision as Data -> Audit Trail as Data -> Enforcement

THE VERDICT IS WRITTEN BEFORE THE PROCESS EXITS, AND THAT ORDER IS DELIBERATE.
A gate that exits non-zero and leaves nothing behind forces the next person to
re-run it to find out what happened -- and a re-run observes a different moment.
The artifact is the record; the exit code is only how the shell learns of it.

TWO DESTINATIONS, BECAUSE THEY ANSWER DIFFERENT QUESTIONS.

    .artifacts/verdicts/<stamp>.json   what did THIS run decide, in full
    .artifacts/ledger.jsonl            what has this platform decided, in order

A directory of per-run files answers the first question and cannot answer the
second: files can be deleted, reordered by timestamp collision, or edited with
nothing to notice. The ledger is hash-chained, so alteration is detectable --
which is what turns "Decisions as Data" into an audit trail rather than a pile
of snapshots.

THE CHAIN SUMMARISES; THE ARTIFACT CARRIES THE DETAIL. Putting whole verdicts
into the ledger would make every line enormous and the chain unreadable, so the
record carries the queryable fields and points at the artifact.

`unknown` EXITS NON-ZERO. A check that did not run has not passed, and a build
that treats "could not check" as success is precisely the fail-open shape this
repository removes everywhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from cscie103_olap_oltp.contracts.verdict import Action, Decision, Outcome
from cscie103_olap_oltp.ledger import LEDGER_PATH, append
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


def record(
    decision: Decision,
    *,
    artifacts: Path = ARTIFACTS,
    ledger: Path = LEDGER_PATH,
) -> Path:
    """Persist a decision, and return the artifact it wrote.

    EXTRACTED FROM main() SO IT CAN BE TESTED. A persistence step reachable only
    through a function that ends in SystemExit is a step whose behaviour is
    asserted by nobody.

    THE ARTIFACT IS WRITTEN FIRST. If the ledger append fails -- a held lock, a
    read-only filesystem -- the full verdict still exists on disk, and the
    failure is loud. The reverse order would leave a chain entry pointing at an
    artifact that was never written.
    """
    artifacts.mkdir(parents=True, exist_ok=True)
    stamp = decision.decided_at.strftime("%Y%m%dT%H%M%S%fZ")
    path = artifacts / f"{stamp}.json"
    path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")

    # THE QUERYABLE FIELDS ONLY. Someone filtering this chain asks which
    # contract, what outcome, what the platform did, and how many violations --
    # not for the whole verdict, which the artifact already holds.
    append(
        {
            "contract": decision.verdict.contract,
            "outcome": decision.verdict.outcome,
            "action": decision.action,
            "reason_code": decision.reason_code,
            "policy_revision": decision.verdict.policy_revision,
            "violations": len(decision.verdict.violations),
            "artifact": path.name,
            "decided_at": decision.decided_at.isoformat(),
        },
        path=ledger,
    )

    return path


def main() -> None:
    verdict = evaluate_repository()
    response = RESPONSES[verdict.outcome]
    decision = Decision(
        verdict=verdict,
        action=response.action,
        reason_code=response.reason_code,
    )

    path = record(decision)

    print(verdict.explain())
    print(f"decision: {response.action} -> {path.relative_to(REPO_ROOT)}")
    raise SystemExit(response.exit_code)


if __name__ == "__main__":
    main()
