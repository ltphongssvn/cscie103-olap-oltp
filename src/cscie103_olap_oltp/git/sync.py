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
from pathlib import Path

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


def is_linked_worktree(root: Path) -> bool:
    """Whether `root` is a linked worktree rather than the main one.

    GIT'S OWN COMPARISON, NOT A HEURISTIC. Inside a linked worktree $GIT_DIR
    points at a private directory under .git/worktrees/<name> while
    $GIT_COMMON_DIR points back at the main repository. Equal paths mean main;
    differing paths mean linked.
    """
    private = git("rev-parse", "--absolute-git-dir", cwd=root).stdout.strip()
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
    return bool(private) and private != common.stdout.strip()


def advance_integration_branch(root: Path) -> None:
    """Fast-forward develop WITHOUT checking it out.

    THIS REPLACES AN UNCONDITIONAL `git switch develop`, WHICH CAUSED REAL
    DAMAGE. Run from a linked worktree, that switch moved develop INTO the
    worktree: the main worktree stopped holding it, and the feature branch being
    cleaned up became the checked-out branch, so `branch -d` then failed after
    the pull had already succeeded.

    A REFSPEC FETCH IS THE FIX, and it is what canonical cleanup tools do for a
    bare-parent layout: advance the ref directly, leaving whatever is checked
    out alone. develop has no home worktree here by design -- the main worktree
    is the stable reference and every branch lives in a linked one.

    NON-FAST-FORWARD IS REFUSED by git for a refspec fetch into a local branch,
    which is the same safety `pull --ff-only` provided and the reason divergence
    is reported rather than merged.
    """
    fetched = git(
        "fetch",
        "origin",
        "--prune",
        f"{INTEGRATION_BRANCH}:{INTEGRATION_BRANCH}",
        cwd=root,
    )
    if fetched.returncode != 0:
        print(fetched.stderr.strip(), file=sys.stderr)
        raise SystemExit(
            f"{INTEGRATION_BRANCH} could not fast-forward. It has diverged from "
            "the remote, which sync will not resolve for you."
        )


def main() -> int:
    # NEVER SWITCH BRANCHES, AND THAT IS THE WHOLE CORRECTION.
    #
    # The previous version ran `git switch develop` unconditionally. From a
    # linked worktree that is exactly wrong, and it happened: develop was
    # checked out here, the main worktree lost it, and cleanup then failed
    # trying to delete the branch it was standing on.
    #
    # --prune IS WHAT MAKES [gone] MEAN ANYTHING. Without it a remote branch
    # deleted on merge still has a local remote-tracking ref, so upstream_gone
    # is false for every branch and cleanup finds nothing to do.
    advance_integration_branch(REPO_ROOT)
    print(f"{INTEGRATION_BRANCH} up to date")

    if is_linked_worktree(REPO_ROOT):
        print("(linked worktree: develop was advanced, not checked out)")

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
