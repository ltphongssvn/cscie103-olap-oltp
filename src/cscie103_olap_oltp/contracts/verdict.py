# src/cscie103_olap_oltp/contracts/verdict.py
"""The as-data spine: Evidence, Verdict, Decision.

CONTRACT-FIRST, AND THIS FILE COMES BEFORE THE DOMAIN SCHEMAS ON PURPOSE. Every
later gate -- data quality, grain, SCD2, freshness -- emits into these shapes.
Building the star schema first and retrofitting an evidence envelope would put
the most foundational contract last.

    Policy as Code -> Execution -> Evidence as Data -> Verdict as Data
                   -> Decision as Data -> Enforcement

THE THREE ARE DISTINCT, AND COLLAPSING THEM LOSES THE AUDIT:

    Evidence  what was OBSERVED. Facts, not judgements. An observed digest, a
              row count, an exit code. Evidence survives a change of policy.
    Verdict   what an EVALUATOR concluded from that evidence, against a named
              policy revision. Re-runnable, and comparable across time.
    Decision  what the PLATFORM did about the verdict. A failing verdict may be
              enforced, waived, or quarantined; that choice is its own fact.

WHY OUTCOME IS THREE-VALUED. `unknown` is not a courtesy. A check that could not
run has not passed, and modelling it as a boolean forces that case to be one of
the other two -- either a false failure that people learn to ignore, or a false
pass that manufactures confidence. This project has removed several gates that
reported instead of enforcing; `unknown` is how a gate says "I did not run"
without lying in either direction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# THE ONLY THREE OUTCOMES, AND THE ONLY FOUR ACTIONS. Literals rather than str,
# so a typo is a type error at author time instead of an unmatched branch at
# runtime -- and so a table keyed on one of them must be TOTAL, which is what
# turns "we forgot the new case" into a failed type check.
Outcome = Literal["pass", "fail", "unknown"]
Action = Literal["publish", "quarantine", "halt", "waive"]


class _Frozen(BaseModel):
    """Base for every as-data record.

    frozen        an emitted fact is not editable. A verdict that can be mutated
                  after the fact is not evidence of anything.
    extra=forbid  an unexpected field is an ERROR, not silently dropped. Pydantic
                  strips unknown keys by default, which would let a renamed
                  producer field vanish without a single failing test.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_Frozen):
    """One observation. A fact, not a judgement.

    Deliberately says nothing about whether the observation is good. The same
    evidence evaluated against a stricter policy tomorrow yields a different
    verdict, and that is the property that makes evidence worth keeping.
    """

    rule_id: str = Field(min_length=1, description="Stable id of the rule this pertains to")
    observed: dict[str, Any] = Field(description="What was measured, as structured data")
    source: str = Field(min_length=1, description="What produced this observation")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Violation(_Frozen):
    """A rule that was not satisfied, explained by machine-readable fields.

    A message alone is prose: it cannot be counted, grouped, or trended. The id
    and reason_code are what make Violations as Data true rather than
    aspirational.
    """

    id: str = Field(min_length=1)
    reason_code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class Verdict(_Frozen):
    """What an evaluator concluded, against a named policy revision.

    policy_revision IS NOT DECORATION. Six months later the question is never
    "did it pass" but "what did it check when it passed". A verdict that cannot
    name the rules that produced it is not auditable, and a digest is the only
    answer that cannot drift from the files it describes.
    """

    contract: str = Field(min_length=1, description="What was judged, e.g. 'repository/v1'")
    outcome: Outcome
    reason_code: str = Field(min_length=1, description="Why, in machine-readable form")
    policy_revision: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 over the policy sources that produced this verdict",
    )
    violations: tuple[Violation, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def explain(self) -> str:
        """Render the verdict for a human, FROM the verdict's own fields.

        Not a second narrative. A summary composed independently of the data
        drifts from it, and then two sources disagree about what happened.
        """
        header = f"{self.contract}: {self.outcome} ({self.reason_code})"
        if not self.violations:
            return header
        lines = [f"  {v.id} {v.reason_code}: {v.message}" for v in self.violations]
        return "\n".join([header, *lines])


class Decision(_Frozen):
    """What the platform did about a verdict.

    SEPARATE FROM THE VERDICT BECAUSE THEY CAN DIVERGE. A failing verdict that
    was waived and a failing verdict that halted the pipeline are the same
    verdict and different history. Folding the action into the judgement makes
    the waiver invisible.
    """

    verdict: Verdict
    action: Action
    reason_code: str = Field(min_length=1)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
