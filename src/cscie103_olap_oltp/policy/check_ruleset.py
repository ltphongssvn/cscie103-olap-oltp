# src/cscie103_olap_oltp/policy/check_ruleset.py
"""Gate: is the SERVER actually protecting develop and main?

WHY THIS IS NOT PART OF `check`. It needs network and gh auth, and a local
commit cannot weaken a server-side ruleset. Coupling every commit to both would
block offline work for no benefit, and a gate that fails on a plane is a gate
people bypass.

WHY IT IS NOT A SCHEDULED WORKFLOW EITHER, which is the obvious alternative and
is wrong: GitHub disables scheduled workflows after 60 days without repository
activity, and a successful scheduled run does not itself count as activity. A
security check that silently stops is worse than none, because it manufactures
confidence. Tying it to CI removes the cron dependency entirely.

UNREACHABLE IS `unknown`, NOT `pass`. If gh is missing or the API refuses, the
check did not run -- and a build that treats "could not check" as success is the
fail-open shape this repository removes everywhere else.
"""

from __future__ import annotations

from cscie103_olap_oltp.contracts.verdict import Verdict
from cscie103_olap_oltp.policy.evaluate import policy_revision
from cscie103_olap_oltp.policy.ruleset import fetch_ruleset, ruleset_violations
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT

CONTRACT = "branch-protection/v1"


def main() -> None:
    revision = policy_revision(REPO_ROOT / "policies")

    try:
        document = fetch_ruleset()
    except (RuntimeError, OSError) as error:
        verdict = Verdict(
            contract=CONTRACT,
            outcome="unknown",
            reason_code="RULESET_UNREACHABLE",
            policy_revision=revision,
        )
        print(verdict.explain())
        print(f"  {error}")
        raise SystemExit(1) from error

    violations = tuple(ruleset_violations(document))
    verdict = Verdict(
        contract=CONTRACT,
        outcome="fail" if violations else "pass",
        reason_code="PROTECTION_INSUFFICIENT" if violations else "PROTECTION_VERIFIED",
        policy_revision=revision,
        violations=violations,
    )

    print(verdict.explain())
    raise SystemExit(1 if violations else 0)


if __name__ == "__main__":
    main()
