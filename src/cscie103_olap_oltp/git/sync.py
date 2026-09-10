# src/cscie103_olap_oltp/git/sync.py
"""Return to develop, pull, and remove the branches that are safe to remove.

STATE-DRIVEN, NOT A PIPELINE. The obvious implementation pipes
`git for-each-ref` through awk and deletes whatever reports [gone]. It works
until a worktree holds one of those branches: git refuses -- correctly -- and
the shell stops, leaving the sync half done after the pull has already
succeeded.

THE PLAN IS COMPUTED BEFORE ANYTHING IS DELETED. That makes it printable,
testable without a repository, and impossible to abort partway through with
half the decisions unmade.

WHAT IT REFUSES TO DO SILENTLY: skip a branch. Every branch whose upstream
vanished but which cannot be removed is reported with the reason. A cleanup that
quietly leaves something behind is the failure signal for this entire class of
bug.
"""

from __future__ import annotations

import sys

from pydantic import BaseModel, ConfigDict

from cscie103_olap_oltp.git.env import git
from cscie103_olap_oltp.git.state import INTEGRATION_BRANCH, Branch, RepositoryState, gather
from cscie103_olap_oltp.policy.snapshot import REPO_ROOT


class CleanupPlan(BaseModel):
    """What cleanup intends to do, decided before it does any of it."""

    model_config = ConfigDict(frozen=True)

    remove: tuple[Branch, ...]
    blocked: tuple[Branch, ...]


def plan_cleanup(state: RepositoryState) -> CleanupPlan:
    """Split branches into removable and blocked, leaving the rest alone.

    THREE CATEGORIES, NOT TWO. A branch whose upstream still exists is neither
    removable nor a problem -- it is work in progress, and reporting it every
    run is the noise that makes people stop reading the output.
    """
    return CleanupPlan(remove=state.deletable, blocked=state.blocked)


def main() -> int:
    # SWITCH BEFORE PULLING. Pulling on a feature branch merges develop into it,
    # which is a different operation entirely and not what `sync` means.
    switch = git("switch", INTEGRATION_BRANCH, cwd=REPO_ROOT)
    if switch.returncode != 0:
        print(switch.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"could not switch to {INTEGRATION_BRANCH}")

    # --prune IS WHAT MAKES [gone] MEAN ANYTHING. Without it a remote branch
    # deleted on merge still has a local remote-tracking ref, so upstream_gone
    # is false for every branch and cleanup finds nothing to do.
    pull = git("pull", "--ff-only", "--prune", cwd=REPO_ROOT)
    if pull.returncode != 0:
        print(pull.stdout.strip(), file=sys.stderr)
        print(pull.stderr.strip(), file=sys.stderr)
        raise SystemExit(
            f"{INTEGRATION_BRANCH} could not fast-forward. It has diverged from "
            "the remote, which sync will not resolve for you."
        )
    print(pull.stdout.strip() or f"{INTEGRATION_BRANCH} already up to date")

    plan = plan_cleanup(gather(REPO_ROOT))

    for branch in plan.remove:
        # `-d`, NEVER `-D`. The lowercase form refuses unmerged work, which
        # doubles as proof the merge really happened -- the plan says it did,
        # and git independently agrees. Forcing would discard that check for a
        # command that runs unattended.
        result = git("branch", "-d", branch.name, cwd=REPO_ROOT)
        if result.returncode == 0:
            print(f"removed {branch.name}")
        else:
            # A REFUSAL HERE IS NEWS, because the plan predicted it would
            # succeed. Reported rather than raised, so one surprise does not
            # abort the removals that follow it.
            print(f"could not remove {branch.name}: {result.stderr.strip()}", file=sys.stderr)

    if plan.blocked:
        print("\nnot removed:")
        for branch in plan.blocked:
            print(f"  {branch.name}: {branch.blocked_because}")

    if not plan.remove and not plan.blocked:
        print("nothing to clean up")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
